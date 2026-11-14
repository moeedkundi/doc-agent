from evals.runner import ci_run_url


def test_ci_run_url_only_on_github_actions() -> None:
    env = {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": "42",
    }
    assert ci_run_url(env) == "https://github.com/owner/repo/actions/runs/42"
    assert ci_run_url({}) is None
