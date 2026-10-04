from app.timeutil import normalize_rugby_league


def test_nrl_and_nrlw_from_rugby_league_titles():
    assert normalize_rugby_league("Sydney Roosters - Newcastle Knights Rugby League") == "NRL"
    assert normalize_rugby_league("Sydney Roosters W - Brisbane Broncos W Rugby League") == "NRLW"
    assert normalize_rugby_league("NRL Grand Final") == "NRL"
    assert normalize_rugby_league("Queensland vs New South Wales State of Origin") == "NRL"


def test_internationals_from_nations_or_hints():
    assert normalize_rugby_league("Australia - New Zealand Rugby Union") == "Internationals"
    assert normalize_rugby_league("Kangaroos vs Kiwis Rugby League") == "Internationals"
    assert normalize_rugby_league("Rugby Championship: Argentina v Fiji") == "Internationals"


def test_super_rugby_and_other():
    assert normalize_rugby_league("Super Rugby Pacific: Brumbies vs Chiefs") == "Super Rugby"
    assert normalize_rugby_league("Wigan Warriors - St Helens Super League Rugby League") == "Other"
    assert normalize_rugby_league("Canterbury - Auckland Rugby Union") == "Other"
