// storyboardOptions.js — the style / aspect / genre choices, in ONE place.
//
// These used to live inside ScriptToStoryboard.jsx. The Profile page now lets a
// user pick their usual answers as defaults, and two copies of these lists would
// drift the moment a style was added — the form would offer it and the profile
// wouldn't. Both import from here instead.

export const STYLES = [
  { id: "rough-sketch", label: "✏️ Rough Sketch" },
  { id: "sketch", label: "🖊️ Sketch" },
  { id: "comic", label: "💥 Comic" },
  { id: "cinematic", label: "🎬 Cinematic" },
  { id: "animation-3d", label: "🧸 Animation 3D" },
  { id: "watercolor", label: "🎨 Watercolor Paint" },
];

export const MORE_STYLES = [
  { id: "photo-commercial", label: "📷 Photo / Commercial" },
  { id: "charcoal", label: "🖤 Charcoal Sketch" },
  { id: "dark-anime", label: "🌃 Dark Anime" },
  { id: "flat-vector", label: "🔷 Flat / Vector" },
  { id: "noir", label: "🎞️ Noir" },
  { id: "stick-figure", label: "🏃 Stick Figure" },
  { id: "graphic-novel", label: "📖 Graphic Novel" },
  { id: "custom", label: "＋ Custom" },
];

export const ALL_STYLES = [...STYLES, ...MORE_STYLES];
export const DEFAULT_STYLE = "rough-sketch"; // pre-selected default (highlighted)

// Styles that draw straight from the shot prompts, with NO locked character /
// prop / background reference images — so the cast and props steps are skipped
// entirely. A rough thumbnail has no rendered faces or sets to keep consistent,
// which is exactly why it's cheap: no reference images to generate either.
// Every other style keeps the full cast → props → panels flow unchanged.
export const REFERENCE_FREE_STYLES = new Set(["rough-sketch"]);

export const ASPECTS = [
  { id: "21:9", note: "Ultra-wide" },
  { id: "16:9", note: "Standard HD" },
  { id: "9:16", note: "Mobile" },
  { id: "2:3", note: "Comic page" },
  { id: "1:1", note: "Square" },
];
export const DEFAULT_ASPECT = "16:9"; // pre-selected standard frame

// Genre shapes the story's tone / pacing in the shot breakdown. The first 6 show
// as chips; the rest live behind "＋ More". "default" = no genre bias (let the
// story decide); "custom" = type your own.
export const GENRES = [
  { id: "default", label: "✨ Default" },
  { id: "animation", label: "🎨 Animation" },
  { id: "commercial", label: "📢 Commercial" },
  { id: "documentary", label: "🎥 Documentary" },
  { id: "educational", label: "📚 Educational" },
  { id: "mythology", label: "🏛️ Mythology" },
];

export const MORE_GENRES = [
  { id: "action", label: "💥 Action" },
  { id: "comedy", label: "😄 Comedy" },
  { id: "drama", label: "🎭 Drama" },
  { id: "fantasy", label: "🐉 Fantasy" },
  { id: "horror", label: "👻 Horror" },
  { id: "music-video", label: "🎵 Music Video" },
  { id: "mystery", label: "🔍 Mystery" },
  { id: "romance", label: "💕 Romance" },
  { id: "sci-fi", label: "🚀 Science Fiction" },
  { id: "thriller", label: "⚡ Thriller" },
  { id: "custom", label: "＋ Custom" },
];

export const ALL_GENRES = [...GENRES, ...MORE_GENRES];

// WHO THE FILM IS FOR — the country and the language on screen.
//
// ⚠ THIS IS NOT A DISPLAY SETTING, IT DECIDES WHAT IS DRAWN. Picking India puts
// ₹ on the price tags and Hindi on the shop signs; picking nothing puts NO
// money and NO readable text on any screen in the film, which is deliberate —
// see market.py. An Indian creator's app promo came back priced in dollars
// because there was nowhere to say this.
//
// ⚠ KEEP `MARKET_COUNTRIES` IN STEP WITH `COUNTRIES` IN market.py. The server
// is the authority: it looks the currency and the units up from the code, and
// a code it does not recognise is passed through as free text rather than
// rejected — so a list that drifts loses the money, not the request.
export const MARKET_COUNTRIES = [
  // ⚠ AND THIS LIST HAS ONE CALL SITE NOW: the PROFILE. The board form's
  // country picker was removed — asked on the way to a storyboard, "which
  // market?" is a question about prices that most people cannot answer, so the
  // country is worked out from the language, the account or the script
  // instead (see `LANGUAGE_COUNTRY` in market.py). Neutral wording here for
  // the same reason: this used to read "Not set — show no prices", which made
  // a storyboard form look like a checkout.
  { id: "", label: "Auto — from each script" },
  { id: "IN", label: "🇮🇳 India" },
  { id: "PK", label: "🇵🇰 Pakistan" },
  { id: "BD", label: "🇧🇩 Bangladesh" },
  { id: "LK", label: "🇱🇰 Sri Lanka" },
  { id: "NP", label: "🇳🇵 Nepal" },
  { id: "US", label: "🇺🇸 United States" },
  { id: "CA", label: "🇨🇦 Canada" },
  { id: "GB", label: "🇬🇧 United Kingdom" },
  { id: "AU", label: "🇦🇺 Australia" },
  { id: "NZ", label: "🇳🇿 New Zealand" },
  { id: "AE", label: "🇦🇪 United Arab Emirates" },
  { id: "SA", label: "🇸🇦 Saudi Arabia" },
  { id: "EG", label: "🇪🇬 Egypt" },
  { id: "SG", label: "🇸🇬 Singapore" },
  { id: "MY", label: "🇲🇾 Malaysia" },
  { id: "ID", label: "🇮🇩 Indonesia" },
  { id: "PH", label: "🇵🇭 Philippines" },
  { id: "TH", label: "🇹🇭 Thailand" },
  { id: "VN", label: "🇻🇳 Vietnam" },
  { id: "JP", label: "🇯🇵 Japan" },
  { id: "KR", label: "🇰🇷 South Korea" },
  { id: "CN", label: "🇨🇳 China" },
  { id: "DE", label: "🇩🇪 Germany" },
  { id: "FR", label: "🇫🇷 France" },
  { id: "ES", label: "🇪🇸 Spain" },
  { id: "IT", label: "🇮🇹 Italy" },
  { id: "NL", label: "🇳🇱 Netherlands" },
  { id: "PL", label: "🇵🇱 Poland" },
  { id: "SE", label: "🇸🇪 Sweden" },
  { id: "TR", label: "🇹🇷 Türkiye" },
  { id: "RU", label: "🇷🇺 Russia" },
  { id: "IL", label: "🇮🇱 Israel" },
  { id: "BR", label: "🇧🇷 Brazil" },
  { id: "MX", label: "🇲🇽 Mexico" },
  { id: "AR", label: "🇦🇷 Argentina" },
  { id: "ZA", label: "🇿🇦 South Africa" },
  { id: "NG", label: "🇳🇬 Nigeria" },
  { id: "KE", label: "🇰🇪 Kenya" },
];

// The language on-screen text is written in. Leaving it blank is fine — the
// server falls back to the country's own language, so picking India alone
// already means Hindi signage. Values are sent as plain names, so an unlisted
// language typed by hand would work too if a free-text box is ever added.
//
// ⚠ HINGLISH IS FIRST-CLASS HERE, not a novelty: it is what Indian creators
// actually caption reels in. `plan_agent.LANGUAGES` describes it as Hindi and
// English mixed in LATIN script, and the video half already honours that.
export const MARKET_LANGUAGES = [
  // Overridden at both call sites too: "Auto — from your script" on the board
  // form, which has no country control, and "Country's own language" on the
  // profile, which has one right beside it.
  { id: "", label: "Auto — from your script" },
  { id: "English", label: "English" },
  { id: "Hindi", label: "Hindi" },
  { id: "Hinglish", label: "Hinglish" },
  { id: "Bengali", label: "Bengali" },
  { id: "Tamil", label: "Tamil" },
  { id: "Telugu", label: "Telugu" },
  { id: "Marathi", label: "Marathi" },
  { id: "Gujarati", label: "Gujarati" },
  { id: "Kannada", label: "Kannada" },
  { id: "Malayalam", label: "Malayalam" },
  { id: "Punjabi", label: "Punjabi" },
  { id: "Urdu", label: "Urdu" },
  { id: "Arabic", label: "Arabic" },
  { id: "Spanish", label: "Spanish" },
  { id: "Portuguese", label: "Portuguese" },
  { id: "French", label: "French" },
  { id: "German", label: "German" },
  { id: "Italian", label: "Italian" },
  { id: "Russian", label: "Russian" },
  { id: "Turkish", label: "Turkish" },
  { id: "Indonesian", label: "Indonesian" },
  { id: "Japanese", label: "Japanese" },
  { id: "Korean", label: "Korean" },
  { id: "Chinese", label: "Chinese" },
];

// Roles offered on the profile. Free text is allowed too ("Other"), but a short
// list covers most of a storyboard team and keeps the data comparable.
export const ROLES = [
  "Director",
  "Writer",
  "Storyboard Artist",
  "Animator",
  "Producer",
  "Editor",
  "Student",
  "Hobbyist",
  "Other",
];
