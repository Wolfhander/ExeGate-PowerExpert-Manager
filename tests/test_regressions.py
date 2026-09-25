import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import ExeGate_PowerExpert_Manager as app
import ups_snmp

QT_APP = app.QApplication.instance() or app.QApplication([])


def sample(temp=44, battery=False, connected=True):
    s = app.UPSStatus()
    s.connected, s.temperature, s.utility_fail = connected, temp, battery
    s.battery_charge = 80
    return s


class EventsTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(app.DEFAULT_CONFIG)
        self.config['alerts']['temp_threshold'] = 50
        self.poller = app.UPSPollerThread(self.config)
        self.events = []
        self.poller.event_occurred.connect(self.events.append)

    def feed(self, s):
        self.poller._detect_events(s)
        if s.connected:
            self.poller._prev_status = s

    def temperature_events(self):
        return [e for e in self.events if e.data.get('alert') == 'temp_high']

    def test_threshold_hysteresis(self):
        for temp in (44, 46, 49.9):
            self.feed(sample(temp))
        self.assertEqual(len(self.temperature_events()), 0)
        for temp in (50, 50.5, 49.5, 50.1):
            self.feed(sample(temp))
        self.assertEqual(len(self.temperature_events()), 1)
        self.feed(sample(48))
        self.feed(sample(50))
        self.assertEqual(len(self.temperature_events()), 2)

    def test_first_hot_sample_and_changed_threshold(self):
        self.feed(sample(55))
        self.assertEqual(len(self.temperature_events()), 1)
        self.poller.config['alerts']['temp_threshold'] = 60
        self.feed(sample(56))
        self.assertEqual(len(self.temperature_events()), 1)
        self.poller.config['alerts']['temp_threshold'] = 54
        self.feed(sample(56))
        self.assertEqual(len(self.temperature_events()), 2)

    def test_invalid_sample_does_not_restore_mains(self):
        self.feed(sample(battery=True))
        self.feed(sample(connected=False))
        self.assertEqual(len(self.events), 1)
        self.feed(sample())
        self.assertIn('восстановлено', self.events[-1].message)

    def test_first_low_battery(self):
        s = sample(battery=True); s.battery_low = True
        self.feed(s)
        self.assertEqual({e.data.get('alert') for e in self.events}, {'battery_low', 'utility_fail'})

    def test_device_information_survives_poll(self):
        self.poller._device_info.model = 'Real UPS'
        self.poller._device_info.rated_batt_v = 48
        self.poller._serial = Mock(is_open=True)
        self.poller._send_command = Mock(return_value='(230 230 230 25 50 2.2 40 00000000')
        received = []
        self.poller.status_updated.connect(received.append)
        self.poller._poll_once()
        self.assertEqual(received[0].model, 'Real UPS')
        self.assertEqual(received[0].battery_cells, 24)

    def test_bad_q1_rejected(self):
        for response in ('(230 230 230 25 50 72 nan 00000000', '(230 230 230 25 50 72 40 0000000X'):
            self.assertFalse(app.MegatecParser.parse_q1(response, app.UPSStatus()))

    def test_poll_interval_reloaded(self):
        self.poller._connect = Mock()
        self.poller._disconnect = Mock()
        waits = []
        def poll():
            self.poller.config['connection']['poll_interval'] = 500
        def wait(seconds):
            waits.append(seconds); self.poller._running = False
        self.poller._poll_once = poll
        self.poller._wake = Mock()
        self.poller._wake.wait.side_effect = wait
        self.poller.run()
        self.assertLessEqual(waits[0], .5)

    def test_snmp_poll_publishes_real_sample(self):
        self.poller.config['connection']['type'] = 'snmp'
        received=[]
        self.poller.status_updated.connect(received.append)
        with patch.object(app, 'read_ups', return_value={'utility_fail':True, 'battery_charge':42, 'temperature':51}):
            self.poller._poll_once()
        self.assertTrue(received[0].connected)
        self.assertTrue(received[0].utility_fail)
        self.assertEqual(received[0].battery_charge, 42)


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = copy.deepcopy(app.DEFAULT_CONFIG)
        self.config['alerts'].update(log_file=False, sound=False, popup=False)
        self.config['shutdown']['on_battery_switch'] = True
        self.config['shutdown']['on_battery_switch_delay_sec'] = 120
        self.patches = [patch.object(app.MainWindow, '_start_poller'),
                        patch.object(app.MainWindow, '_setup_tray'),
                        patch.object(app, 'CONFIG_FILE', str(Path(self.temp.name)/'config.json')),
                        patch.object(app, 'LOG_FILE', str(Path(self.temp.name)/'events.csv')),
                        patch.object(app, 'show_notification', return_value=True),
                        patch.object(app, 'remove_notification'),
                        patch.object(app.QApplication, 'beep'),
                        patch.object(app.subprocess, 'run', return_value=Mock(returncode=0))]
        self.started=[p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.win = app.MainWindow(self.config)
        self.win._sd_check_timer.stop()
        self.addCleanup(self.win.deleteLater)

    def test_cancel_stays_cancelled_until_mains_returns(self):
        self.win._check_auto_shutdown(sample(battery=True))
        self.assertEqual(self.win._sd_phase, 1)
        self.win._cancel_auto_shutdown()
        self.win._check_auto_shutdown(sample(battery=True))
        self.assertEqual(self.win._sd_phase, 0)
        self.win._check_auto_shutdown(sample())
        self.win._check_auto_shutdown(sample(battery=True))
        self.assertEqual(self.win._sd_phase, 1)

    def test_disconnect_does_not_cancel(self):
        self.win._check_auto_shutdown(sample(battery=True))
        self.win._check_auto_shutdown(sample(connected=False))
        self.assertEqual(self.win._sd_phase, 1)

    def test_mains_cancels_windows_command(self):
        self.win._execute_shutdown()
        self.assertTrue(self.win._sd_windows_pending)
        self.win._check_auto_shutdown(sample())
        self.assertEqual(self.win._sd_phase, 0)
        app.subprocess.run.assert_called_with(['shutdown', '/a'], timeout=5, capture_output=True)

    def test_failed_command_reported_and_not_retried_each_poll(self):
        app.subprocess.run.return_value.returncode = 5
        self.win._execute_shutdown()
        self.win._check_auto_shutdown(sample(battery=True))
        self.assertEqual(self.win._sd_phase, 0)
        self.assertTrue(self.win._sd_suppressed)
        self.assertIn('код возврата 5', self.win.tab_events._events[-1].message)

    def test_failed_windows_cancel_keeps_pending(self):
        self.win._execute_shutdown()
        app.subprocess.run.return_value.returncode=5
        self.assertFalse(self.win._cancel_auto_shutdown())
        self.assertTrue(self.win._sd_windows_pending)
        self.assertEqual(self.win._sd_phase, 2)

    def test_warning_uses_config_and_monotonic_deadline(self):
        self.win.config['shutdown']['warn_before_sec'] = 37
        with patch.object(app.time, 'monotonic', return_value=100):
            self.win._arm_shutdown('battery_switch', 120, 'test')
        before=len(self.win.tab_events._events)
        with patch.object(app.time, 'monotonic', return_value=184):
            self.win._auto_shutdown_tick()
            self.win._auto_shutdown_tick()
        self.assertEqual(self.win._sd_countdown, 36)
        self.assertEqual(len(self.win.tab_events._events), before+1)

    def test_missed_timer_ticks_execute_once(self):
        self.win._arm_shutdown('battery_switch', 120, 'test')
        with patch.object(app.time, 'monotonic', return_value=self.win._sd_deadline+4):
            self.win._auto_shutdown_tick()
            self.win._auto_shutdown_tick()
        self.assertEqual(app.subprocess.run.call_count, 1)

    def test_disabling_trigger_cancels_countdown(self):
        self.win._check_auto_shutdown(sample(battery=True))
        config=copy.deepcopy(self.win.config)
        config['shutdown']['on_battery_switch']=False
        self.win._on_config_changed(config)
        self.assertEqual(self.win._sd_phase, 0)

    def test_alert_switches_mute_popups_but_keep_journal(self):
        self.win.config['alerts'].update(popup=True, sound=True)
        for key in ('temp_high', 'battery_low', 'utility_fail'):
            self.win.config['alerts'][key]=False
            self.win._on_event(app.EventRecord('WARNING', key, {'alert':key}))
        app.show_notification.assert_not_called()
        app.QApplication.beep.assert_not_called()
        self.assertEqual(len(self.win.tab_events._events), 3)

    def test_popup_silent_flag(self):
        self.win.config['alerts'].update(popup=True, sound=False)
        self.win._on_event(app.EventRecord('WARNING', 'hot', {'alert':'temp_high'}))
        self.assertFalse(app.show_notification.call_args.args[-1])
        app.QApplication.beep.assert_not_called()

    def test_sound_without_popup(self):
        self.win.config['alerts'].update(popup=False,sound=True)
        self.win._on_event(app.EventRecord('WARNING','hot'))
        app.QApplication.beep.assert_called_once()
        app.show_notification.assert_not_called()

    def test_log_setting(self):
        self.win._on_event(app.EventRecord('INFO','not saved'))
        self.assertFalse(Path(app.LOG_FILE).exists())
        self.win.config['alerts']['log_file']=True
        self.win._on_event(app.EventRecord('INFO','saved'))
        self.assertIn('saved',Path(app.LOG_FILE).read_text(encoding='utf-8'))
        self.assertNotIn('not saved',Path(app.LOG_FILE).read_text(encoding='utf-8'))

    def test_changed_connection_reconnects(self):
        self.win.poller=Mock()
        config=copy.deepcopy(self.win.config); config['connection']['port']='COM99'
        self.win._on_config_changed(config)
        self.win.poller.request_reconnect.assert_called_once()
        config['connection']['port']='COM88'
        self.assertEqual(self.win.poller.config['connection']['port'],'COM99')

    def test_reset_updates_widgets_and_preserves_defaults(self):
        baseline=copy.deepcopy(app.DEFAULT_CONFIG)
        tab=self.win.tab_settings
        tab.spin_temp_thr.setValue(67)
        tab.spin_sd_warn.setValue(123)
        with patch.object(app.QMessageBox,'question',return_value=app.QMessageBox.Yes), patch.object(app.QMessageBox,'information'):
            tab._reset()
        self.assertEqual(tab.spin_temp_thr.value(),45)
        self.assertEqual(tab.spin_sd_warn.value(),60)
        saved=json.loads(Path(app.CONFIG_FILE).read_text(encoding='utf-8'))
        self.assertEqual(saved['alerts']['temp_threshold'],45)
        self.assertEqual(app.DEFAULT_CONFIG,baseline)

    def test_failed_save_not_applied(self):
        tab=self.win.tab_settings
        tab.spin_temp_thr.setValue(63)
        with patch('builtins.open', side_effect=OSError('read only')), patch.object(app.QMessageBox,'warning'):
            tab._save()
        self.assertEqual(self.win.config['alerts']['temp_threshold'],45)

    def test_missing_temperature_renders(self):
        self.win._on_status(sample(float('nan')))
        self.assertIn('—',self.win.tab_dash.card_temp._val_lbl.text())


class SnmpTests(unittest.TestCase):
    def values(self, **changes):
        v={'source':'5','battery_status':'3','battery_charge':'27', 'runtime_min':'9',
           'battery_voltage':'725','input_freq':'500','temperature':'46'}
        v.update(changes)
        return {ups_snmp.OIDS[k]:v for k,v in v.items()}

    def test_units_and_flags(self):
        s=ups_snmp.decode_ups(self.values())
        self.assertEqual(s['battery_voltage'],72.5)
        self.assertEqual(s['input_freq'],50)
        self.assertEqual(s['runtime_min'],9)
        self.assertTrue(s['utility_fail'])
        self.assertTrue(s['battery_low'])

    def test_unknown_state_rejected(self):
        for changes in ({'source':'1'},{'battery_status':'No Such Instance'},{'battery_charge':'nan'}):
            with self.assertRaises(ValueError):
                ups_snmp.decode_ups(self.values(**changes))

    def test_configured_connection_used(self):
        c={'snmp_host':'192.0.2.1','snmp_community':'test','snmp_port':1161,'timeout':2}
        with patch.object(ups_snmp,'snmp_get',return_value=self.values()) as get:
            ups_snmp.read_ups(c)
        self.assertEqual(get.call_args.args[3:],(1161,2))

    def test_legacy_shutdown_command_migrates_only_exact_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'
            with patch.object(app,'CONFIG_FILE',str(path)):
                path.write_text(json.dumps({'shutdown':{'command':'shutdown /s /t 60 /c "ИБП: питание от батареи. Завершение работы."'}}),encoding='utf-8')
                self.assertIn('/t 0',app.load_config()['shutdown']['command'])
                path.write_text(json.dumps({'shutdown':{'command':'custom-command'}}),encoding='utf-8')
                self.assertEqual(app.load_config()['shutdown']['command'],'custom-command')



class IntegrationTests(unittest.TestCase):
    def test_local_snmp_exchange(self):
        from snmp_test_agent import query_fixture
        self.assertEqual(query_fixture()['temperature'],46)

    def test_snmp_connect_does_not_require_serial(self):
        config=copy.deepcopy(app.DEFAULT_CONFIG)
        config['connection']['type']='snmp'
        poller=app.UPSPollerThread(config)
        with patch.object(app,'SERIAL_AVAILABLE',False):
            poller._connect()
        self.assertFalse(poller._demo_mode)

    def test_port_probe_remains_static(self):
        with patch.object(app.serial,'Serial') as serial:
            self.assertTrue(app.PortKiller._try_open('COM-test'))
            serial.assert_called_once()

    def test_snmp_control_cannot_report_success(self):
        config=copy.deepcopy(app.DEFAULT_CONFIG)
        config['connection']['type']='snmp'
        p=app.UPSPollerThread(config)
        events=[]
        p.event_occurred.connect(events.append)
        p.send_test_short()
        self.assertEqual(len(events),1)
        self.assertEqual(events[0].level,'WARNING')

    def test_native_notification_sound_flag_and_unicode_limit(self):
        import tray_notifications as tray
        import ctypes
        self.assertEqual(ctypes.sizeof(tray.NOTIFYICONDATA),976)
        with patch.object(tray,'_notify',return_value=True) as notify:
            tray.show_notification(1,'title','🌡️'*200,sound=False)
            data=notify.call_args.args[1]
            self.assertTrue(data.dwInfoFlags & 0x10)
            self.assertLessEqual(len(data.szInfo.encode('utf-16-le')),510)
            tray.show_notification(1,'title','message',sound=True)
            self.assertFalse(notify.call_args.args[1].dwInfoFlags & 0x10)


if __name__ == '__main__':
    unittest.main()
