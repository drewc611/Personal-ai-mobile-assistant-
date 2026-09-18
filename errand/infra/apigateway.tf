resource "aws_apigatewayv2_api" "telegram" {
  name          = "errand-telegram"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "telegram" {
  api_id                 = aws_apigatewayv2_api.telegram.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ingress.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "telegram" {
  api_id = aws_apigatewayv2_api.telegram.id
  # POST only. Telegram posts; anything else is somebody looking around.
  route_key = "POST /telegram"
  target    = "integrations/${aws_apigatewayv2_integration.telegram.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.telegram.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    # A personal assistant does not receive hundreds of updates a second.
    # This is the cheapest possible brake on somebody hammering the endpoint,
    # and it sits in front of the secret check rather than behind it.
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId = "$context.requestId"
      status    = "$context.status"
      route     = "$context.routeKey"
      latency   = "$context.responseLatency"
      # No request body and no sender id: the access log is not where message
      # content should end up.
    })
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/errand-telegram"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.errand.arn
}

resource "aws_lambda_permission" "api" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingress.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.telegram.execution_arn}/*/*"
}
