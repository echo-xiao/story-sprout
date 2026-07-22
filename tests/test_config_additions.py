import importlib


def test_new_config_defaults(monkeypatch):
    # Clear so defaults apply
    for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
              "ACCESS_CODE", "GCS_SA_JSON", "GEMINI_IMAGE_MODEL", "GEMINI_VISION_MODEL"):
        monkeypatch.delenv(k, raising=False)
    import src.config as cfg
    importlib.reload(cfg)
    assert cfg.DEEPSEEK_BASE_URL == "https://api.deepseek.com"
    assert cfg.DEEPSEEK_MODEL == "deepseek-chat"
    assert cfg.ACCESS_CODE == "Caput Draconis"
    assert cfg.GEMINI_IMAGE_MODEL == "gemini-3-pro-image"
    assert cfg.GEMINI_VISION_MODEL  # non-empty
    assert cfg.GCS_SA_JSON == ""


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("ACCESS_CODE", "Alohomora")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
    import src.config as cfg
    importlib.reload(cfg)
    assert cfg.ACCESS_CODE == "Alohomora"
    assert cfg.DEEPSEEK_MODEL == "deepseek-reasoner"
