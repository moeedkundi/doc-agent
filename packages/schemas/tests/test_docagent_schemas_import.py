import docagent_schemas


def test_package_imports() -> None:
    assert docagent_schemas.__version__ == "0.1.0"
