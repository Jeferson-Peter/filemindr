from filemindr.core.runner import run_pipeline


def test_import():
    assert callable(run_pipeline)
