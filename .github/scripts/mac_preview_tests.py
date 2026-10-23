#!/usr/bin/env python3
"""Execute real Chromium tests and smoke the mounted, packaged app, without real profiles."""
import argparse
import base64
import http.server
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'build/src/out/Default'
LOG = ROOT / 'preview-logs'
BROWSER_SUITES = (
    'ProfileMenuViewWorkspaceTest', 'LittleArcBrowserTest',
    'LittleArcDisabledBrowserTest', 'AppControllerLittleArcBrowserTest',
    'AppControllerLittleArcStartupBrowserTest',
)
UNIT_SUITES = ('GitHubLiveFolderControllerTest', 'HeliumArcPreviewIdentity')
REPEATS = 3


def expected_tests(suites):
    """Read the pinned authored patch tests, not a parallel hand-written model."""
    tests = set()
    patch_roots = [ROOT / 'helium-chromium/patches', ROOT / 'patches']
    for patches, line in [(p, line) for p in patch_roots for line in (p / 'series').read_text().splitlines()]:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        content = '\n'.join(x[1:] for x in (patches / line).read_text().splitlines()
                            if x.startswith('+') and not x.startswith('+++'))
        for suite, case in re.findall(
                r'(?:IN_PROC_BROWSER_TEST_F|TEST_F|TEST)\(\s*(\w+)\s*,\s*(\w+)\s*\)', content):
            if suite in suites:
                tests.add(suite + '.' + case)
    for suite in suites:
        assert any(t.startswith(suite + '.') for t in tests), f'Missing source tests for {suite}'
    return tests


def verify_summary(path, expected):
    result = json.loads(path.read_text())
    iterations = result.get('per_iteration_data', [])
    assert len(iterations) == REPEATS, f'Expected {REPEATS} iterations, got {len(iterations)}'
    for number, iteration in enumerate(iterations, 1):
        assert set(iteration) == expected, f'Iteration {number} missing or unexpected tests: {set(iteration) ^ expected}'
        for name, attempts in iteration.items():
            assert len(attempts) == 1 and attempts[0]['status'] == 'SUCCESS', (name, attempts)
    return sorted(expected)


def native():
    results = {}
    for binary, suites in [('browser_tests', BROWSER_SUITES), ('unit_tests', UNIT_SUITES)]:
        expected = expected_tests(suites)
        summary = LOG / f'{binary}-summary.json'
        command = [str(OUT / binary), '--gtest_filter=' + ':'.join(s + '.*' for s in suites),
                   '--test-launcher-jobs=1', '--test-launcher-retry-limit=0',
                   f'--gtest_repeat={REPEATS}', '--test-launcher-bot-mode',
                   '--test-launcher-summary-output=' + str(summary)]
        print('Running:', ' '.join(command), flush=True)
        subprocess.run(command, cwd=ROOT / 'build/src', check=True, timeout=1800)
        results[binary] = {'tests': verify_summary(summary, expected), 'iterations': REPEATS,
                           'all_passed_without_retries': True}
    (LOG / 'native-test-results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2), flush=True)


class Fixture(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        page = (f'<!doctype html><meta charset="utf-8"><title>{self.path}</title>'
                '<input id="draft"><a href="/second">Second page</a>').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *_):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def smoke(binary):
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    driver_port = free_port()
    driver_url = f'http://127.0.0.1:{driver_port}'
    driver_log = (LOG / 'chromedriver.log').open('w')
    driver = subprocess.Popen([str(OUT / 'chromedriver'), f'--port={driver_port}',
                               '--allowed-ips=127.0.0.1'], stdout=driver_log,
                              stderr=subprocess.STDOUT)

    def request(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(driver_url + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=90) as response:
            value = json.load(response).get('value')
        if isinstance(value, dict) and value.get('error'):
            raise RuntimeError(value)
        return value

    try:
        for _ in range(100):
            if driver.poll() is not None:
                raise RuntimeError('ChromeDriver exited before startup')
            try:
                request('GET', '/status')
                break
            except (OSError, urllib.error.URLError):
                time.sleep(.2)
        else:
            raise RuntimeError('ChromeDriver startup timed out')
        with tempfile.TemporaryDirectory(prefix='helium-packaged-smoke-',
                                         dir=os.environ['RUNNER_TEMP']) as data_root:
            def run_profile(name, expect_marker):
                caps = {'capabilities': {'alwaysMatch': {'browserName': 'chrome',
                        'goog:chromeOptions': {'binary': str(binary), 'args': [
                            '--user-data-dir=' + str(Path(data_root) / name),
                            '--no-first-run', '--no-default-browser-check',
                            '--use-mock-keychain', '--disable-background-networking',
                            '--enable-features=HeliumProfileWindowSwitching,HeliumLittleArc']}}}}
                session = request('POST', '/session', caps)['sessionId']
                base = '/session/' + session
                try:
                    request('POST', base + '/url', {'url': origin + '/first'})
                    def js(script):
                        return request('POST', base + '/execute/sync', {'script': script, 'args': []})
                    assert js('return document.title') == '/first'
                    assert js('return localStorage.getItem("marker")') == expect_marker
                    if name == 'profile-b':
                        assert js('return document.cookie') == ''
                    js('localStorage.setItem("marker", "' + name + '"); '
                       'document.cookie="isolation=' + name + '; SameSite=Lax"; '
                       'document.querySelector("input").value="unsaved draft"')
                    assert js('return document.querySelector("input").value') == 'unsaved draft'
                    # Native browser screenshot is recorded by macOS separately; this is page content.
                    screenshot = request('GET', base + '/screenshot')
                    (LOG / f'packaged-{name}.png').write_bytes(base64.b64decode(screenshot))
                    subprocess.run(['/usr/sbin/screencapture', '-x',
                                    str(LOG / f'native-desktop-{name}.png')],
                                   check=True, timeout=15)
                    request('POST', base + '/url', {'url': origin + '/second'})
                    assert js('return document.title') == '/second'
                    request('POST', base + '/back', {})
                    assert js('return document.title') == '/first'
                    assert js('return localStorage.getItem("marker")') == name
                    request('POST', base + '/forward', {})
                    assert js('return document.title') == '/second'
                    if name == 'profile-a' and expect_marker is None:
                        original_handle = request('GET', base + '/window')
                        before_handles = set(request('GET', base + '/window/handles'))
                        external_url = origin + '/external-link-preview'
                        # Launch Services targets this exact app, not the default
                        # browser. No AppleScript, Accessibility, or Automation grant.
                        subprocess.run(['/usr/bin/open', '-a', str(Path(binary).parents[2]),
                                        external_url, '--args',
                                        '--user-data-dir=' + str(Path(data_root) / name),
                                        '--no-first-run', '--no-default-browser-check',
                                        '--use-mock-keychain',
                                        '--enable-features=HeliumProfileWindowSwitching,HeliumLittleArc'],
                                       check=True, timeout=20)
                        deadline = time.monotonic() + 30
                        while time.monotonic() < deadline:
                            new_handles = set(request('GET', base + '/window/handles')) - before_handles
                            if new_handles:
                                break
                            time.sleep(.2)
                        else:
                            raise AssertionError('OS URL event did not create a new tracked preview window')
                        assert len(new_handles) == 1, 'OS event unexpectedly created multiple windows'
                        popup = new_handles.pop()
                        request('POST', base + '/window', {'handle': popup})
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline:
                            if request('GET', base + '/url') == external_url:
                                break
                            time.sleep(.2)
                        else:
                            raise AssertionError('External preview did not navigate to the fixture URL')
                        bounds = request('GET', base + '/window/rect')
                        assert 700 <= bounds['width'] <= 740, bounds
                        assert 620 <= bounds['height'] <= 660, bounds
                        assert js('return localStorage.getItem("marker")') == 'profile-a'
                        assert 'isolation=profile-a' in js('return document.cookie')
                        subprocess.run(['/usr/sbin/screencapture', '-x',
                                        str(LOG / 'native-little-arc-external-url.png')],
                                       check=True, timeout=15)
                        request('DELETE', base + '/window')
                        assert set(request('GET', base + '/window/handles')) == before_handles
                        request('POST', base + '/window', {'handle': original_handle})
                        assert request('GET', base + '/url') == origin + '/second'
                        print('Direct macOS URL event, bounded same-profile preview, close and original-window preservation passed.', flush=True)
                finally:
                    request('DELETE', base)
            run_profile('profile-a', None)
            run_profile('profile-b', None)
            run_profile('profile-a', 'profile-a')
        report = {
            'passed': True, 'app': 'Read-only mounted DMG Helium Arc Preview.app',
            'mode': 'GUI ChromeDriver, sandbox left enabled, local HTTP fixture only',
            'checks': ['ARM64 packaged app starts', 'local fixture navigation',
                       'back/forward', 'script and unsaved-input state',
                       'two disposable data directories isolate storage/cookies',
                       'first disposable profile persists across relaunch',
                       'direct macOS external URL event creates 720x640 preview',
                       'external preview shares only its source profile storage',
                       'closing external preview preserves the original window'],
            'not_claimed': ['native visual QA', 'cold-start/multi-URL OS event matrix',
                            'native profile-menu UI E2E', 'native live-folder menu UI E2E'],
        }
        (LOG / 'packaged-smoke.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2), flush=True)
    finally:
        driver.terminate()
        try:
            driver.wait(timeout=15)
        except subprocess.TimeoutExpired:
            driver.kill()
            driver.wait()
        driver_log.close()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['native', 'smoke'])
    parser.add_argument('binary', nargs='?')
    args = parser.parse_args()
    LOG.mkdir(exist_ok=True)
    if args.mode == 'native':
        native()
    else:
        assert args.binary, 'Packaged app executable is required'
        smoke(Path(args.binary))
