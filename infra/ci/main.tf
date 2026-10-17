# GitHub Actions -> AWS without stored keys (OIDC), applied once by hand:
#
#   plan role    pull requests: read-only, runs `terraform plan`
#   deploy role  only jobs in the GitHub environment "dev": runs `terraform apply`,
#                limited to resources named apply-relay-*
#
# Roles the deploy role creates (the Lambda roles) must carry the permissions boundary
# below, so a workflow can never create a role more powerful than the services need.

locals {
  prefix       = "apply-relay"
  account_id   = data.aws_caller_identity.current.account_id
  github_oidc  = "token.actions.githubusercontent.com"
  repo_subject = "repo:${var.github_repository}"
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------- OIDC provider

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url            = "https://${local.github_oidc}"
  client_id_list = ["sts.amazonaws.com"]
  # AWS validates GitHub's certificate itself; the thumbprint is required by the API but unused.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://${local.github_oidc}"
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

data "aws_iam_policy_document" "trust" {
  for_each = {
    plan   = ["${local.repo_subject}:pull_request", "${local.repo_subject}:ref:refs/heads/main"]
    deploy = ["${local.repo_subject}:environment:${var.deploy_environment}"]
  }

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc}:sub"
      values   = each.value
    }
  }
}

# ---------------------------------------------------------------- state access (both roles)

data "aws_iam_policy_document" "state" {
  statement {
    sid       = "ListStateBucket"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${local.prefix}-tfstate-*"]
  }

  statement {
    sid       = "ReadWriteState"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::${local.prefix}-tfstate-*/*"]
  }

  # Even a plan takes the lock, so the read-only plan role needs to write it too.
  statement {
    sid       = "StateLock"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:DescribeTable"]
    resources = ["arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-tfstate-*-locks"]
  }
}

# ---------------------------------------------------------------- plan role (pull requests)

resource "aws_iam_role" "plan" {
  name                 = "${local.prefix}-github-plan"
  assume_role_policy   = data.aws_iam_policy_document.trust["plan"].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "plan_read_only" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "plan" {
  source_policy_documents = [data.aws_iam_policy_document.state.json]

  # ReadOnlyAccess can read data too. A plan never needs candidates' CVs or applications.
  statement {
    sid       = "NoPersonalData"
    effect    = "Deny"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.prefix}-*-cvs-*/*"]
  }

  statement {
    sid     = "NoApplicationData"
    effect  = "Deny"
    actions = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-*-applications",
      "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-*-parsed-cvs",
      "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-*-matches",
      "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-*-notifications",
      "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-*-audit-log",
    ]
  }
}

resource "aws_iam_role_policy" "plan" {
  name   = "terraform-plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}

# ---------------------------------------------------------------- deploy role (environment "dev")

resource "aws_iam_role" "deploy" {
  name                 = "${local.prefix}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.trust["deploy"].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy" {
  source_policy_documents = [data.aws_iam_policy_document.state.json]

  statement {
    sid       = "ReadEverything"
    actions   = ["sns:Get*", "sns:List*", "sqs:Get*", "sqs:List*", "lambda:Get*", "lambda:List*", "dynamodb:Describe*", "dynamodb:List*", "s3:ListAllMyBuckets", "apigateway:GET", "logs:Describe*", "logs:ListTagsForResource", "cloudwatch:Describe*", "cloudwatch:ListTagsForResource", "iam:Get*", "iam:List*", "kms:DescribeKey", "budgets:ViewBudget", "budgets:ListTagsForResource"]
    resources = ["*"]
  }

  statement {
    sid     = "ManageOwnResources"
    actions = ["sns:*", "sqs:*", "lambda:*", "dynamodb:*", "logs:*", "cloudwatch:*"]
    resources = [
      "arn:aws:sns:${var.region}:${local.account_id}:${local.prefix}-*",
      "arn:aws:sqs:${var.region}:${local.account_id}:${local.prefix}-*",
      "arn:aws:lambda:${var.region}:${local.account_id}:function:${local.prefix}-*",
      "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-*",
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/${local.prefix}-*",
      "arn:aws:cloudwatch:${var.region}:${local.account_id}:alarm:${local.prefix}-*",
      "arn:aws:cloudwatch::${local.account_id}:dashboard/${local.prefix}-*",
    ]
  }

  statement {
    sid       = "SavedLogQueries" # query definitions have random IDs
    actions   = ["logs:PutQueryDefinition", "logs:DeleteQueryDefinition", "logs:DescribeQueryDefinitions"]
    resources = ["*"]
  }

  statement {
    sid       = "SesIdentities" # verifying an address only sends it a confirmation link
    actions   = ["ses:VerifyEmailIdentity", "ses:DeleteIdentity", "ses:GetIdentityVerificationAttributes"]
    resources = ["*"]
  }

  statement {
    sid       = "EventSourceMappings" # their ARNs are random UUIDs, so they can't be scoped by name
    actions   = ["lambda:CreateEventSourceMapping", "lambda:UpdateEventSourceMapping", "lambda:DeleteEventSourceMapping", "lambda:GetEventSourceMapping", "lambda:TagResource", "lambda:UntagResource"]
    resources = ["*"]
  }

  statement {
    sid       = "HttpApis" # API IDs are random too; limited to this region
    actions   = ["apigateway:GET", "apigateway:POST", "apigateway:PUT", "apigateway:PATCH", "apigateway:DELETE", "apigateway:TagResource", "apigateway:UntagResource"]
    resources = ["arn:aws:apigateway:${var.region}::/apis", "arn:aws:apigateway:${var.region}::/apis/*", "arn:aws:apigateway:${var.region}::/tags/*"]
  }

  statement {
    sid     = "OwnBuckets"
    actions = ["s3:*"]
    resources = [
      "arn:aws:s3:::${local.prefix}-*-cvs-*",
      "arn:aws:s3:::${local.prefix}-*-cvs-*/*",
    ]
  }

  statement {
    sid       = "Budget"
    actions   = ["budgets:ModifyBudget", "budgets:TagResource", "budgets:UntagResource"]
    resources = ["arn:aws:budgets::${local.account_id}:budget/${local.prefix}-*"]
  }

  # Roles: only apply-relay-* roles, only with the boundary, and only passed to Lambda.
  statement {
    sid       = "CreateRolesWithBoundary"
    actions   = ["iam:CreateRole", "iam:PutRolePolicy", "iam:AttachRolePolicy", "iam:PutRolePermissionsBoundary"]
    resources = ["arn:aws:iam::${local.account_id}:role/${local.prefix}-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.lambda_boundary.arn]
    }
  }

  statement {
    sid       = "ManageOwnRoles"
    actions   = ["iam:DeleteRole", "iam:DeleteRolePolicy", "iam:DetachRolePolicy", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/${local.prefix}-*"]
  }

  statement {
    sid       = "PassRolesToLambda"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/${local.prefix}-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }

  # The CI roles themselves, the boundary and the state bucket are off limits to CI.
  statement {
    sid    = "ProtectCiAndState"
    effect = "Deny"
    actions = [
      "iam:*",
      "s3:DeleteBucket", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:PutBucketVersioning", "s3:PutLifecycleConfiguration",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/${local.prefix}-github-*",
      aws_iam_policy.lambda_boundary.arn,
      "arn:aws:s3:::${local.prefix}-tfstate-*",
    ]
  }

  statement {
    sid       = "KeepBoundaries"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "terraform-deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

# ---------------------------------------------------------------- Lambda permissions boundary

# The most any service role can ever do, whatever its own policy says.
data "aws_iam_policy_document" "lambda_boundary" {
  statement {
    sid     = "ServiceData"
    actions = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "s3:GetObject", "s3:PutObject", "sns:Publish", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "ses:SendEmail"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-*",
      "arn:aws:s3:::${local.prefix}-*-cvs-*/*",
      "arn:aws:sns:${var.region}:${local.account_id}:${local.prefix}-*",
      "arn:aws:sqs:${var.region}:${local.account_id}:${local.prefix}-*",
      "arn:aws:ses:${var.region}:${local.account_id}:identity/*",
    ]
  }

  statement {
    sid       = "SnsKey"
    actions   = ["kms:GenerateDataKey*", "kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["sns.${var.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "Observability"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "lambda_boundary" {
  name        = "${local.prefix}-lambda-boundary"
  description = "Permissions boundary for every Lambda role created by CI."
  policy      = data.aws_iam_policy_document.lambda_boundary.json
}
