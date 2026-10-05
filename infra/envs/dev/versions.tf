terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
  }

  # Bucket and region come from backend.hcl (see backend.hcl.example):
  #   terraform init -backend-config=backend.hcl
  backend "s3" {
    key          = "dev/terraform.tfstate"
    use_lockfile = true # S3 native locking, no DynamoDB lock table needed
    encrypt      = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "job-application-events"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
