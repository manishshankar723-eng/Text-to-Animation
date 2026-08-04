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
