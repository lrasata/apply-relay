# Copy to dev.tfvars (git-ignored) and fill in.
alert_email        = "you@example.com"
monthly_budget_usd = 5

# Deploy one service at a time: add the next one, then plan and apply again.
# Order: application, cv-parser, matching, notification, audit. Remove this line to deploy all five.
services = ["application"]
