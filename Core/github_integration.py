from github import Github


def create_github_issue(repo_name, token, rca_report):
    try:
        g = Github(token)
        repo = g.get_repo(repo_name)
        repo.create_issue(
            title=f"🚨 Production Incident: {rca_report['classification']}",
            body=f"AutoRCA detected an issue.\n\n**Report:**\n{rca_report['details']}",
        )
        return True
    except Exception as e:
        print(f"GitHub Error: {e}")
        return False
