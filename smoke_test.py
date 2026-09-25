"""Safe packaged-app smoke test: no UPS access, no shutdown, isolated config."""
import copy
import json
from pathlib import Path


def run(app, output):
    from PyQt5.QtTest import QTest

    class TestWindow(app.MainWindow):
        def _start_poller(self):
            pass
        def _setup_tray(self):
            pass

    # The Windows offscreen Qt platform does not enumerate system fonts.
    import os
    for filename in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf", "consolab.ttf", "seguiemj.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
        if font.exists():
            app.QFontDatabase.addApplicationFont(str(font))
    config = copy.deepcopy(app.DEFAULT_CONFIG)
    config['alerts'].update(popup=False, sound=False, log_file=False)
    window = TestWindow(config)
    window._sd_check_timer.stop()
    errors = []
    original_hook = app.sys.excepthook
    app.sys.excepthook = lambda kind, value, tb: errors.append(str(value))
    try:
        poller = app.UPSPollerThread(config)
        config['alerts']['temp_threshold'] = 50
        poller.config = copy.deepcopy(config)
        events = []
        poller.event_occurred.connect(events.append)
        status = poller._demo_status()
        status.temperature = 46
        poller._detect_events(status)
        assert not events, '46 degrees must not alert at threshold 50'
        poller._prev_status = copy.deepcopy(status)
        status.temperature = 50
        poller._detect_events(status)
        assert len(events) == 1, '50 degrees must alert'
        window.config = config
        window._on_status(status)
        window.show()
        QTest.qWait(300)
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            QTest.qWait(50)
        window.tabs.setCurrentIndex(0)
        QTest.qWait(50)
        path = Path(output).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(path.with_suffix('.png')))
        assert not errors, errors
        # Exercise PySNMP's packaged imports/transport using only a local fake agent.
        from snmp_test_agent import query_fixture
        assert query_fixture()['temperature'] == 46
        result = {'version': app.APP_VERSION, 'status':'passed', 'checks':[
            '46 C below 50 C threshold', '50 C threshold reached',
            'all five tabs rendered', 'no Qt callback exceptions',
            'SNMP loopback query and unit conversion'], 'hardware_access':False}
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0
    except Exception as exc:
        Path(output).write_text(json.dumps({'status':'failed','error':str(exc)}),encoding='utf-8')
        return 1
    finally:
        app.sys.excepthook = original_hook
        window._clock_timer.stop()
        window.hide()
        window.deleteLater()
