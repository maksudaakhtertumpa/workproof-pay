#!/usr/bin/env bash
# Creates a small sample client project ("Kopi Labs website") with 15 commits spread
# over the last 7 days, to demo WorkProof Pay's "GitHub commits" proof-of-work source.
#
# Usage:  bash scripts/make_demo_repo.sh ["your-git-email"]
# Result: ~/Documents/kopi-labs-website (a git repo, ready to push to an empty GitHub repo)

set -euo pipefail

DEST="$HOME/Documents/kopi-labs-website"
EMAIL="${1:-$(git config user.email || echo demo@example.com)}"
NAME="$(git config user.name || echo 'WorkProof Demo')"

if [ -e "$DEST" ]; then
  echo "Folder already exists: $DEST"
  echo "Rename or remove it first, then run this again."
  exit 1
fi

mkdir -p "$DEST"
cd "$DEST"
git init -q -b main
git config user.email "$EMAIL"
git config user.name "$NAME"

# date helper: N days ago at HH:MM (works on macOS and Linux)
when() {
  if date -v-1d >/dev/null 2>&1; then
    date -v-"$1"d -v"$2"H -v"$3"M -v0S "+%Y-%m-%dT%H:%M:%S"
  else
    date -d "$1 days ago $2:$3" "+%Y-%m-%dT%H:%M:%S"
  fi
}

commit() {  # commit "<days ago>" "<HH>" "<MM>" "<message>"
  git add -A
  GIT_AUTHOR_DATE="$(when "$1" "$2" "$3")" GIT_COMMITTER_DATE="$(when "$1" "$2" "$3")" \
    git commit -q -m "$4"
  echo "  ✓ $4"
}

mkdir -p css js img

cat > README.md <<'EOF'
# Kopi Labs website

Sample client project used to demo [WorkProof Pay](https://github.com/maksudaakhtertumpa/workproof-pay):
a small café website with online booking. The commit history is the freelancer's proof of work.
EOF
cat > index.html <<'EOF'
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Kopi Labs</title>
<link rel="stylesheet" href="css/style.css"></head>
<body><header><h1>Kopi Labs</h1></header><main></main></body></html>
EOF
echo "body { font-family: system-ui, sans-serif; margin: 0; }" > css/style.css
commit 7 10 12 "Set up project structure and base page"

cat >> css/style.css <<'EOF'
:root { --brown: #4b2e1e; --cream: #fff8ef; }
header { background: var(--brown); color: var(--cream); padding: 24px; }
EOF
commit 7 14 40 "Add brand colors and header styles"

cat > index.html <<'EOF'
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Kopi Labs</title>
<link rel="stylesheet" href="css/style.css"></head>
<body>
<header><h1>Kopi Labs</h1><nav><a href="#menu">Menu</a> <a href="#book">Book a table</a></nav></header>
<section class="hero"><h2>Small-batch coffee in Penang</h2><p>Roasted on site every morning.</p>
<a class="cta" href="#book">Book a table</a></section>
<main></main></body></html>
EOF
cat >> css/style.css <<'EOF'
.hero { padding: 80px 24px; text-align: center; background: var(--cream); }
.cta { display: inline-block; padding: 12px 24px; background: var(--brown); color: #fff; border-radius: 6px; }
EOF
commit 6 9 30 "Redesign landing page hero section with call to action"

cat >> css/style.css <<'EOF'
nav a { color: var(--cream); margin-left: 16px; text-decoration: none; }
EOF
commit 6 16 5 "Add navigation links to header"

cat > menu.json <<'EOF'
[{"name": "Kopi O", "price": 3.5}, {"name": "Flat white", "price": 9}, {"name": "Kaya toast", "price": 6}]
EOF
cat > js/menu.js <<'EOF'
fetch("menu.json").then(r => r.json()).then(items => {
  const ul = document.createElement("ul");
  ul.id = "menu";
  items.forEach(i => { const li = document.createElement("li"); li.textContent = `${i.name} · RM ${i.price}`; ul.append(li); });
  document.querySelector("main").append(ul);
});
EOF
commit 5 11 20 "Build menu section loaded from menu.json"

cat > booking.html <<'EOF'
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Book a table · Kopi Labs</title>
<link rel="stylesheet" href="css/style.css"></head>
<body><h1 id="book">Book a table</h1>
<form id="booking"><label>Name <input name="name" required></label>
<label>Date <input type="date" name="date" required></label>
<label>Guests <input type="number" name="guests" min="1" max="12" value="2"></label>
<button>Request booking</button></form>
<script src="js/booking.js"></script></body></html>
EOF
commit 4 10 0 "Build online booking form with date picker"

cat > js/booking.js <<'EOF'
const form = document.getElementById("booking");
form.addEventListener("submit", (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  if (new Date(data.date) < new Date().setHours(0, 0, 0, 0)) return alert("Please pick a future date.");
  fetch("/api/bookings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
    .then(() => form.replaceWith("Thanks! We'll confirm by email."));
});
EOF
commit 4 15 45 "Validate booking dates and submit to bookings API"

cat > js/notify.js <<'EOF'
// Sends the café an email for each new booking (called by the bookings API)
export async function notifyCafe(booking, sendEmail) {
  const subject = `New booking: ${booking.name}, ${booking.guests} guests on ${booking.date}`;
  return sendEmail({ to: "hello@kopilabs.example", subject, text: JSON.stringify(booking, null, 2) });
}
EOF
commit 4 18 10 "Connect booking form to email notifications"

cat >> css/style.css <<'EOF'
form { display: grid; gap: 12px; max-width: 360px; margin: 24px auto; }
input, button { font: inherit; padding: 10px; }
EOF
commit 3 9 50 "Style booking form"

cat >> css/style.css <<'EOF'
img { max-width: 100%; height: auto; }
.gallery img { aspect-ratio: 4 / 3; object-fit: cover; }
EOF
cat >> index.html <<'EOF'
<!-- gallery images now lazy-load -->
<section class="gallery"><img loading="lazy" src="img/bar.webp" alt="Coffee bar"><img loading="lazy" src="img/roaster.webp" alt="Roaster"></section>
EOF
commit 3 14 30 "Fix slow image loading on mobile: lazy-load and serve WebP"

cat >> css/style.css <<'EOF'
@media (max-width: 600px) { .hero { padding: 48px 16px; } nav a { margin-left: 8px; } }
EOF
commit 2 11 15 "Fix header and hero layout on small screens"

cat > js/hours.js <<'EOF'
const hours = { weekday: "7:30–18:00", weekend: "8:00–20:00" };
document.querySelector("main").insertAdjacentHTML("beforeend",
  `<p class="hours">Open weekdays ${hours.weekday}, weekends ${hours.weekend}</p>`);
EOF
commit 2 16 40 "Add opening hours from client feedback"

sed -i.bak 's/Small-batch coffee in Penang/Small-batch coffee, roasted in George Town/' index.html && rm -f index.html.bak
commit 1 10 5 "Update hero copy after client review call"

cat > robots.txt <<'EOF'
User-agent: *
Allow: /
EOF
cat >> index.html <<'EOF'
<!-- meta: description added for search -->
EOF
commit 1 15 20 "Add SEO basics: robots.txt and meta description"

sed -i.bak 's/Request booking/Book now/' booking.html && rm -f booking.html.bak
commit 0 9 40 "Polish booking button text and final QA fixes"

echo
echo "Done: $(git rev-list --count HEAD) commits in $DEST"
