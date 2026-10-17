# Copy to ci.tfvars (git-ignored) and fill in.
github_repository = "lrasata/apply-relay"

# This repository uses GitHub's immutable OIDC subject (owner and repo IDs in the "sub" claim).
# Read yours: GET https://api.github.com/repos/<owner>/<name>/actions/oidc/customization/sub
github_subject_prefix = "repo:lrasata@63764427/apply-relay@1414376056"

# The account already has the GitHub OIDC provider (another project created it).
create_oidc_provider = false
