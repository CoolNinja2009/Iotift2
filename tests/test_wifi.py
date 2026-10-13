"""First-class WiFi: `wifi name do ... end`, its events, properties and retry policy."""

import os

from helpers import errors, error_text, run, texts, serial, check_source, compile_source

HOME = '''
wifi home do
    ssid: "Workshop"
    password: "hunter2hunter2"
    retry: backoff(1s, max: 4s)
    timeout: 5s
end
'''


def wifi_calls(lines):
    return [(int(ln[1:ln.index(']')]), ln.split('] ', 1)[1]) for ln in lines if '] WiFi.' in ln]


def test_connects_and_reports_events_and_properties():
    src = HOME + '''
on home.connected do
    println("up {home.ip} {home.rssi} {home.state}")
end

on home.lost do
    println("lost {home.connected} {home.ip}")
end
'''
    lines = run(src, 3000, events='300:W:got_ip,2000:W:lost,2100:W:got_ip')
    assert serial(lines) == [(300, 'up 192.168.1.50 -50 connected'), (2000, 'lost false '),
                             (2100, 'up 192.168.1.50 -50 connected')]
    begins = [t for t, c in wifi_calls(lines) if c.startswith('WiFi.begin(Workshop)')]
    assert begins == [0, 2000]          # reconnects at once after losing the link


def test_retries_with_backoff_after_timeouts():
    src = HOME + '''
on home.connected do
    println("up")
end
'''
    lines = run(src, 30000, events='26000:W:got_ip')
    begins = [t for t, c in wifi_calls(lines) if c.startswith('WiFi.begin')]
    # 5s per attempt, then pauses of 1s, 2s, 4s, 4s
    assert begins == [0, 6000, 13000, 22000]
    assert texts(lines) == ['up']


def test_fixed_retry_and_state_while_waiting():
    src = HOME.replace('backoff(1s, max: 4s)', 'fixed(3s)') + '''
every 1s do
    println("{home.state}")
end
'''
    lines = run(src, 9500)
    begins = [t for t, c in wifi_calls(lines) if c.startswith('WiFi.begin')]
    assert begins == [0, 8000]
    assert texts(lines)[:7] == ['connecting'] * 4 + ['retrying'] * 3


def test_retry_none_gives_up_and_connect_starts_again():
    src = HOME.replace('backoff(1s, max: 4s)', 'none') + '''
on home.failed do
    println("failed {home.state}")
end

after 7s do
    home.connect()
end
'''
    lines = run(src, 8000)
    assert texts(lines) == ['failed failed']
    begins = [t for t, c in wifi_calls(lines) if c.startswith('WiFi.begin')]
    assert begins == [0, 7000]


def test_disconnect_is_not_a_lost_link():
    src = HOME + '''
on home.connected do
    println("up")
    home.disconnect()
end

on home.lost do
    println("lost")
end

every 1s do
    println("{home.state}")
end
'''
    lines = run(src, 2500, events='300:W:got_ip,301:W:lost')
    assert texts(lines) == ['up', 'idle', 'idle']


def test_autoconnect_false_waits_for_connect():
    src = HOME.replace('    timeout: 5s\n', '    timeout: 5s\n    autoconnect: false\n') + '''
after 500ms do
    home.connect()
end
'''
    lines = run(src, 1000)
    assert [t for t, c in wifi_calls(lines) if c.startswith('WiFi.begin')] == [500]


def test_wait_until_connected_in_a_task():
    src = HOME + '''
task report do
    wait until home.connected timeout 2s
    println("first: {home.connected}")
    wait until home.connected
    println("then: {home.ip}")
end
'''
    assert texts(run(src, 4000, events='3000:W:got_ip')) == ['first: false', 'then: 192.168.1.50']


def test_setup_calls():
    src = HOME.replace('    timeout: 5s\n', '    hostname: "oven-1"\n    power_save: none\n')
    calls = [c for _, c in wifi_calls(run(src, 5))]
    assert calls[:3] == ['WiFi.setHostname(oven-1)', 'WiFi.mode(1)', 'WiFi.begin(Workshop)']


def test_access_point_and_client_events():
    src = '''
wifi setup_ap do
    mode: ap
    ssid: "Oven-Setup"
    password: "changeme123"
    channel: 6
    max_clients: 2
end

on setup_ap.client_joined do
    println("joined {setup_ap.clients} at {setup_ap.ip}")
end

on setup_ap.client_left do
    println("left {setup_ap.clients}")
end
'''
    lines = run(src, 1000, events='100:W:join,200:W:join,500:W:leave')
    assert texts(lines) == ['joined 1 at 192.168.4.1', 'joined 2 at 192.168.4.1', 'left 1']
    assert ('WiFi.softAP(Oven-Setup,changeme123,ch6,max2)' in [c for _, c in wifi_calls(lines)])


def test_station_and_access_point_together():
    src = HOME + '''
wifi setup_ap do
    mode: ap
    ssid: "Oven-Setup"
end
'''
    calls = [c for _, c in wifi_calls(run(src, 5))]
    assert 'WiFi.mode(3)' in calls
    assert 'WiFi.softAP(Oven-Setup,(open),ch1,max4)' in calls


def test_wifi_on_core_zero():
    src = '''
core 0 as network do
    wifi home do
        ssid: "Workshop"
    end

    on home.connected do
        println("up on core 0")
    end
end
'''
    assert texts(run(src, 500, events='100:W:got_ip')) == ['up on core 0']


def test_wifi_events_follow_the_wifi_core():
    src = '''
core 0 as network do
    wifi home do
        ssid: "Workshop"
    end
end

on home.connected do
    println("up")
end
'''
    assert texts(run(src, 500, events='100:W:got_ip')) == ['up']


def test_cross_core_use_is_rejected():
    src = '''
core 0 as network do
    wifi home do
        ssid: "Workshop"
    end
end

every 1s do
    println("{home.connected}")
end
'''
    text = error_text(src)
    assert '`home` belongs to core 0 (network), but this timer runs on core 1' in text
    assert 'shared var' in text


def test_secret_from_environment():
    src = HOME.replace('"hunter2hunter2"', 'secret("IOT_TEST_WIFI_PASS")')
    assert 'secret `IOT_TEST_WIFI_PASS` is not set' in error_text(src)
    os.environ['IOT_TEST_WIFI_PASS'] = 'from-the-env'
    try:
        assert errors(src) == []
        assert '"from-the-env"' in compile_source(src, 'test.iot', files={})
        assert 'IOT_TEST_WIFI_PASS' not in compile_source(src, 'test.iot', files={})
    finally:
        del os.environ['IOT_TEST_WIFI_PASS']


def test_secret_from_dot_env_file(tmp_path):
    (tmp_path / '.env').write_text('# local secrets\nWIFI_PASS="dotenv-secret"\n')
    src = HOME.replace('"hunter2hunter2"', 'secret("WIFI_PASS")')
    main = tmp_path / 'main.iot'
    main.write_text(src)
    code = compile_source(src, str(main))
    assert '"dotenv-secret"' in code


def test_setting_errors():
    bad = '''
wifi home do
    ssid: "Workshop"
    password: "short"
    hostname: "-bad-"
    retry: sometimes
    speed: 10
end
'''
    text = error_text(bad)
    assert 'WPA2 passwords are 8 to 63 characters' in text
    assert 'a hostname is 1 to 32 letters' in text
    assert '`retry` is `none`, `forever`, `fixed(5s)` or `backoff(1s, max: 1min)`' in text
    assert 'a WiFi station has no setting `speed`' in text
    assert any('needs an `ssid`' in m for m in errors('wifi home do\n    password: "12345678"\nend\n'))
    ap = 'wifi ap1 do\n    mode: ap\n    ssid: "x"\n    retry: none\n    channel: 14\nend\n'
    text = error_text(ap)
    assert 'a WiFi access point has no setting `retry`' in text
    assert '`channel` is 1 to 13' in text


def test_one_station_only():
    src = HOME + 'wifi other do\n    ssid: "B"\nend\n'
    assert any('one WiFi station; `home` is already one' in m for m in errors(src))


def test_unknown_event_property_and_method():
    src = HOME + '''
on home.joined do
    pass
end

on boot do
    println("{home.speed}")
    home.reconnect()
    let x = home
end
'''
    text = error_text(src)
    assert '`home` has no event `joined`' in text
    assert '`home` has no property `speed`' in text
    assert '`home` has no method `reconnect`' in text
    assert '`home` is a WiFi connection, not a value' in text


def test_state_can_be_matched():
    src = HOME + '''
every 1s do
    match home.state do
        WifiState.connected => println("on")
        WifiState.connecting | WifiState.retrying => println("trying")
        _ => println("off")
    end
end
'''
    assert texts(run(src, 2500, events='1500:W:got_ip')) == ['trying', 'on']


def test_adc2_conflicts_with_wifi_on_esp32():
    src = HOME + 'pin pot = adc 25\n'
    assert any('GPIO 25 is on ADC2' in m for m in errors(src))
    assert errors(HOME + 'pin pot = adc 34\n') == []
    assert errors('device esp32s3\n' + HOME + 'pin pot = adc 15\n') == []


def test_no_wifi_on_esp32h2():
    assert any('no WiFi radio' in m for m in errors('device esp32h2\n' + HOME))


def test_wifi_is_still_usable_as_a_name():
    assert errors('var wifi: i32 = 3\n\non boot do\n    println("{wifi}")\nend\n') == []
