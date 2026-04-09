# Shared launcher keyboard builder for Telegram hooks.
# Usage:
#   LAUNCHER_REPLY_MARKUP=$(build_launcher_reply_markup "$CHAT_ID" "$BOTTOKEN")

build_cpc_auth_token() {
  local chat_id="$1"
  local bot_token="$2"
  local now
  local jwt_header
  local jwt_payload
  local jwt_signature

  now=$(date +%s)
  jwt_header=$(printf '%s' '{"alg":"HS256","typ":"JWT"}' | base64 -w0 | tr '+/' '-_' | tr -d '=')
  jwt_payload=$(printf '{"sub":"%s","iat":%s,"exp":%s}' "$chat_id" "$now" "$((now + 604800))" | base64 -w0 | tr '+/' '-_' | tr -d '=')
  jwt_signature=$(printf '%s' "${jwt_header}.${jwt_payload}" | openssl dgst -sha256 -hmac "${bot_token}" -binary | base64 -w0 | tr '+/' '-_' | tr -d '=')

  printf '%s' "${jwt_header}.${jwt_payload}.${jwt_signature}"
}

build_launcher_reply_markup() {
  local chat_id="$1"
  local bot_token="$2"
  local cpc_auth_token

  cpc_auth_token=$(build_cpc_auth_token "$chat_id" "$bot_token")

  jq -cn --arg token "$cpc_auth_token" '{
    keyboard: [[
      {text: "⚡️\nActions"},
      {text: "📱\nDevelop", web_app: {url: ("https://cpc.claude.do/dev/?token=" + $token)}},
      {text: "🎙️\nVoice", web_app: {url: ("https://cpc.claude.do/#voice&token=" + $token)}},
      {text: "📝\nScrum"}
    ]],
    resize_keyboard: true,
    is_persistent: true
  }'
}
