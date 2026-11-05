#!/usr/bin/env bash
# Greps the codebase for design bans from CLAUDE.md. Exits 1 if any ban is broken.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB="$ROOT/apps/web"
SRC=("$WEB/app" "$WEB/components" "$WEB/lib" "$WEB/styles" "$WEB/middleware.ts")
failed=0

ban() {
  local name="$1" pattern="$2"
  shift 2
  local hits
  hits="$(grep -rnEi --include='*.ts' --include='*.tsx' --include='*.css' --include='*.json' --include='*.mjs' \
    "$pattern" "$@" 2>/dev/null)"
  if [ -n "$hits" ]; then
    echo "BANNED: $name"
    echo "$hits" | sed 's/^/  /' | head -20
    failed=1
  fi
}

# Icons, kits, Tailwind.
ban "icon library" 'lucide|heroicons|react-icons|@tabler|phosphor|font-?awesome' "${SRC[@]}" "$WEB/package.json"
ban "ui kit or tailwind" 'tailwind|shadcn|@radix-ui|@mui/|chakra-ui|antd|bootstrap' "${SRC[@]}" "$WEB/package.json"
ban "inline svg" '<svg|\.svg["'"'"')]' "${SRC[@]}"

# Decoration.
ban "border-radius off the token" 'border(-[a-z]+)*-radius: *[^0v; 2]' "$WEB/app" "$WEB/components"
ban "box-shadow" 'box-shadow' "${SRC[@]}"
ban "gradient" '(linear|radial|conic)-gradient' "${SRC[@]}"
ban "backdrop-filter" 'backdrop-filter' "${SRC[@]}"
ban "filter" '[^-]filter: *[a-z]' "$WEB/app" "$WEB/components" "$WEB/styles"
ban "text-shadow" 'text-shadow' "${SRC[@]}"

# Fonts and colour.
ban "banned font" 'Inter[," ]|Geist|Poppins|Roboto|Montserrat|Space Grotesk|Instrument Serif|DM Sans|Manrope' "${SRC[@]}"
ban "system font stack" 'font-family:[^;]*(system-ui|-apple-system|BlinkMacSystemFont)' "${SRC[@]}"
ban "banned colour" 'purple|indigo|violet' "${SRC[@]}"
ban "colour outside tokens" '#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(' "$WEB/app" "$WEB/components" "$WEB/lib"
ban "font size off the scale" 'font-size: *[0-9.]' "$WEB/app" "$WEB/components"
ban "motion off the spec" 'transition:[^;]*[0-9]+m?s|ease-(in|out)|ease;' "$WEB/app" "$WEB/components"

# Runtime network calls leave the department's network.
ban "external url" 'https?://' "${SRC[@]}"
ban "external font" 'fonts\.(googleapis|gstatic)' "${SRC[@]}" "$WEB/next.config.ts"

# Copy. The API's messages are copy too.
COPY=(seamless unlock empower leverage cutting-edge revolutionary 'ai-powered' magic smart effortless journey elevate harness)
ban "copy word" "\\b($(IFS='|'; echo "${COPY[*]}"))\\b" "$WEB/app" "$WEB/components" "$WEB/lib" "$ROOT/services/api/app/routers" "$ROOT/services/api/app/schemas.py"
ban "copy: oops" 'oops' "$WEB/app" "$WEB/components" "$ROOT/services/api/app/routers"

# No exclamation marks in anything a person reads.
hits="$(grep -rnE --include='*.tsx' --include='*.py' '[A-Za-z]!["<]|>[^<{]*![^=<{]*<' "$WEB/app" "$WEB/components" "$ROOT/services/api/app/routers" 2>/dev/null)"
if [ -n "$hits" ]; then echo "BANNED: exclamation mark"; echo "$hits" | sed 's/^/  /' | head; failed=1; fi

# Non-ASCII outside Odia and the allowed marks: catches emoji and pictogram glyphs.
hits="$(find "$WEB/app" "$WEB/components" "$WEB/lib" -type f \( -name '*.ts' -o -name '*.tsx' -o -name '*.css' \) -print0 \
  | xargs -0 perl -CSD -ne 'print "$ARGV:$.: $_" if /[^\x00-\x7F\x{0B00}-\x{0B7F}\x{2014}\x{2013}\x{00D7}\x{00B7}]/; close ARGV if eof')"
if [ -n "$hits" ]; then echo "BANNED: emoji or glyph"; echo "$hits" | sed 's/^/  /' | head; failed=1; fi

# Emblems and badges are off limits: CLAUDE.md, and the State Emblem of India Act.
ban "dropped looks" 'Redaction|Big Shoulders|Baker-Miller|--wall|--flag' "${SRC[@]}"

# Light is the default. Night shift is the only dark surface, and only through tokens.
hits="$(grep -rln -- 'var(--saffron)\|var(--india-green)' "$WEB/app" "$WEB/components" | grep -vE 'Shell\.module\.css|sign-in/page\.module\.css|search/page\.module\.css|search/\[id\]/page\.module\.css')"
if [ -n "$hits" ]; then echo "BANNED: national colours outside the stripe, face boxes and lead notice"; echo "$hits" | sed 's/^/  /'; failed=1; fi

if [ "$failed" -ne 0 ]; then
  echo "lint:design failed"
  exit 1
fi
echo "lint:design passed"
