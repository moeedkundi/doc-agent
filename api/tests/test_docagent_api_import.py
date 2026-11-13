import docagent_api


def test_package_imports() -> None:
    assert docagent_api.__version__ == "0.1.0"
