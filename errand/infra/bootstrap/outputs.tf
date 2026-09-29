output "deploy_role_arn" {
  description = "Set this as the AWS_DEPLOY_ROLE repository variable (not a secret -- an ARN is not sensitive, and the role only trusts this repo's named environment)."
  value       = aws_iam_role.deploy.arn
}

output "permissions_boundary_arn" {
  description = "The ceiling on anything the deploy role creates."
  value       = aws_iam_policy.boundary.arn
}

output "state_bucket" {
  value = aws_s3_bucket.state.id
}

output "state_lock_table" {
  value = aws_dynamodb_table.state_lock.name
}

output "backend_hcl" {
  description = "Write this to errand/infra/backend.hcl, then `terraform init -backend-config=backend.hcl`."
  value       = <<-EOT
    bucket         = "${aws_s3_bucket.state.id}"
    key            = "errand/v0/terraform.tfstate"
    region         = "${var.region}"
    dynamodb_table = "${aws_dynamodb_table.state_lock.name}"
    encrypt        = true
  EOT
}

output "next_steps" {
  value = <<-EOT

    1. Create the GitHub Environment "${var.github_environment}" at
       Settings > Environments, and add yourself as a required reviewer.
       Nothing can deploy until you click approve.

    2. Add a repository VARIABLE (Settings > Secrets and variables > Actions >
       Variables), not a secret:
           AWS_DEPLOY_ROLE = ${aws_iam_role.deploy.arn}

    3. Write the backend config and initialise the main stack:
           terraform -chdir=.. init -backend-config=backend.hcl

    4. Fill in errand/infra/terraform.tfvars, starting from
       terraform.tfvars.example. The deploy will refuse to run until the model
       ids, the owner identity for the channel you picked, the budget and the
       tier 3 cap are all set.

  EOT
}
