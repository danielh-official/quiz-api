# Quiz API on AWS Lambda behind an HTTP API on a Cloudflare-managed domain. Run through deploy/aws.sh, which builds and
# pushes the image and feeds these variables from .env.aws. State is local (terraform.tfstate, gitignored): it holds
# the secrets below in plain text, so it stays on this machine.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 6.0" }
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.0" }
  }
}

provider "aws" {
  region = "us-east-1" # next to the Neon database (AWS us-east-1)
}

provider "cloudflare" {} # token from CLOUDFLARE_API_TOKEN: Zone.DNS edit on the zone only

variable "domain_name" {
  type        = string
  description = "Public hostname, e.g. quiz-api.danielhaven.com; its parent must be a Cloudflare zone"
}
variable "image_uri" {
  type        = string
  default     = "" # set by deploy/aws.sh after pushing; empty only for the first, ECR-only apply
  description = "ECR image by digest"
}
variable "database_url" {
  type      = string
  sensitive = true
}
variable "github_client_id" {
  type = string
}
variable "github_client_secret" {
  type      = string
  sensitive = true
}
variable "jwt_signing_key" {
  type      = string
  sensitive = true
}
variable "storage_encryption_key" {
  type      = string
  sensitive = true
}
variable "allowed_users" {
  type = string
}
variable "plugin_marketplace" {
  type    = string
  default = ""
}
variable "budget_email" {
  type        = string
  default     = ""
  description = "Where the monthly budget alert goes; empty skips the budget"
}

locals {
  name = "quiz-api"
  zone = join(".", slice(split(".", var.domain_name), 1, length(split(".", var.domain_name))))
}

# Image registry. Each deploy pushes a new image; only the last few are kept.

resource "aws_ecr_repository" "app" {
  name         = local.name
  force_delete = true
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 3 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 3 }
      action       = { type = "expire" }
    }]
  })
}

# Function: the Dockerfile's prod image, with Lambda Web Adapter turning invocations into HTTP requests.

resource "aws_iam_role" "lambda" {
  name = "${local.name}-lambda"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "app" {
  function_name = local.name
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = ["arm64"]
  memory_size   = 1024 # more memory means more CPU: faster cold starts, still well inside the free allowance
  timeout       = 30   # the HTTP API gives up at 30s anyway
  # ponytail: migrations run on every cold start (the image's CMD). Fine at personal scale; if concurrent cold starts
  # ever race a new migration, run `alembic upgrade head` in deploy/aws.sh and set image_config.command here.

  environment {
    variables = {
      APP_URL                      = "https://${var.domain_name}"
      DATABASE_URL                 = var.database_url
      GITHUB_CLIENT_ID             = var.github_client_id
      GITHUB_CLIENT_SECRET         = var.github_client_secret
      JWT_SIGNING_KEY              = var.jwt_signing_key
      STORAGE_ENCRYPTION_KEY       = var.storage_encryption_key
      ALLOWED_USERS                = var.allowed_users
      PLUGIN_MARKETPLACE           = var.plugin_marketplace
      AWS_LWA_READINESS_CHECK_PATH = "/up"
      AWS_LWA_ASYNC_INIT           = "true" # migrations + imports may outlast Lambda's 10s init phase
      HOME                         = "/tmp" # the only writable path; Lambda's user has no home directory
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy_attachment.logs]
}

# HTTP API: every route goes to the function; the default execute-api URL is off, so only the domain answers.

resource "aws_apigatewayv2_api" "app" {
  name                         = local.name
  protocol_type                = "HTTP"
  disable_execute_api_endpoint = true
}

resource "aws_apigatewayv2_integration" "app" {
  api_id                 = aws_apigatewayv2_api.app.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.app.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "app" {
  api_id    = aws_apigatewayv2_api.app.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.app.id}"
}

resource "aws_apigatewayv2_stage" "app" {
  api_id      = aws_apigatewayv2_api.app.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "api" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.app.execution_arn}/*/*"
}

# Domain: ACM certificate validated through Cloudflare, then the API's domain and its CNAME. Records are DNS only
# (not proxied), so TLS ends at AWS.

data "cloudflare_zone" "zone" {
  filter = { name = local.zone }
}

resource "aws_acm_certificate" "app" {
  domain_name       = var.domain_name
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

resource "cloudflare_dns_record" "validation" {
  for_each = { for o in aws_acm_certificate.app.domain_validation_options : o.domain_name => o }
  zone_id  = data.cloudflare_zone.zone.zone_id
  name     = trimsuffix(each.value.resource_record_name, ".")
  type     = each.value.resource_record_type
  content  = trimsuffix(each.value.resource_record_value, ".")
  ttl      = 1 # automatic
  proxied  = false
  comment  = "ACM validation for ${var.domain_name} (Terraform, quiz-api)"
}

resource "aws_acm_certificate_validation" "app" {
  certificate_arn         = aws_acm_certificate.app.arn
  validation_record_fqdns = [for r in cloudflare_dns_record.validation : r.name]
}

resource "aws_apigatewayv2_domain_name" "app" {
  domain_name = var.domain_name
  domain_name_configuration {
    certificate_arn = aws_acm_certificate_validation.app.certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}

resource "aws_apigatewayv2_api_mapping" "app" {
  api_id      = aws_apigatewayv2_api.app.id
  domain_name = aws_apigatewayv2_domain_name.app.id
  stage       = aws_apigatewayv2_stage.app.id
}

resource "cloudflare_dns_record" "app" {
  zone_id = data.cloudflare_zone.zone.zone_id
  name    = var.domain_name
  type    = "CNAME"
  content = aws_apigatewayv2_domain_name.app.domain_name_configuration[0].target_domain_name
  ttl     = 1 # automatic
  proxied = false
  comment = "Quiz API on AWS (Terraform, quiz-api)"
}

# AWS has no hard spending cap: email when the month's spend passes $1 or is forecast to pass $5. Counts only the
# services this stack uses, so the rest of the account's bill (subscriptions, other projects) doesn't trip it.
# ponytail: filters by service, so another project's Lambda or API Gateway spend counts too; tag the resources and
# filter on an activated cost allocation tag if that ever matters.

resource "aws_budgets_budget" "monthly" {
  count        = var.budget_email == "" ? 0 : 1
  name         = local.name
  budget_type  = "COST"
  limit_amount = "5"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name = "Service" # names as Cost Explorer shows them
    values = [
      "AWS Lambda",
      "Amazon API Gateway",
      "Amazon EC2 Container Registry (ECR)",
      "AmazonCloudWatch",
      "AWS Certificate Manager",
    ]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 20
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}

output "repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "url" {
  value = "https://${var.domain_name}"
}
