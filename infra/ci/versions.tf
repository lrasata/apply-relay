terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  # Same state bucket as the environments, different key:
  #   terraform init -backend-config=../envs/dev/backend.hcl
  backend "s3" {
    key          = "ci/terraform.tfstate"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "job-application-events"
      ManagedBy = "terraform"
      Component = "ci"
    }
  }
}
