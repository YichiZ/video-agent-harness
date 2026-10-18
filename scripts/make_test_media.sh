#!/usr/bin/env bash
# Build the generated test media. Nothing here is committed (tests/media/ is gitignored).
#
#   scripts/make_test_media.sh [--force] [OUT_DIR]
#
# Writes to OUT_DIR (default tests/media/):
#   cuts.mp4      5 four-second clips at 640x360, 25 fps, no audio, so the
#                 4 cuts fall at frames 100, 200, 300 and 400 (4 s, 8 s, 12 s, 16 s).
#                 Clips come from the Video-MME videos when VIDEO_MME_DIR has them,
#                 otherwise each missing clip is a solid colour.
#   over_cap.mp4  61 minutes of 2x2 px black at 1 fps (about 100 KB), for the
#                 60-minute cap. (ffmpeg's colour source rejects 1x1.)
#   windowed.mp4  25 minutes of the same, so step 7 captions it in two windows.
#
# Existing files are kept unless --force is given.
set -euo pipefail

force=0
if [[ "${1:-}" == "--force" ]]; then force=1; shift; fi
out="${1:-$(dirname "$0")/../tests/media}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
mme="${VIDEO_MME_DIR:-$HOME/data/video-mme}"
mme="${mme/#\~/$HOME}"

need() { [[ $force == 1 || ! -s "$out/$1" ]]; }

if need over_cap.mp4; then
  ffmpeg -v error -y -f lavfi -i "color=c=black:size=2x2:rate=1" -t 3660 \
    -c:v libx264 -pix_fmt yuv420p -tune stillimage "$out/over_cap.mp4"
  echo "wrote $out/over_cap.mp4"
fi

if need windowed.mp4; then
  ffmpeg -v error -y -f lavfi -i "color=c=black:size=2x2:rate=1" -t 1500 \
    -c:v libx264 -pix_fmt yuv420p -tune stillimage "$out/windowed.mp4"
  echo "wrote $out/windowed.mp4"
fi

if need cuts.mp4; then
  # Each clip: Video-MME id, start second, fallback colour. Adjacent clips come
  # from different videos so every boundary is a hard cut. The start times are
  # checked with step 6 to have no cuts inside a clip.
  clips=(
    "TbaCxIJ_VP4 20 red"
    "t6V9i8fFADI 60 green"
    "Sm0lziolZyM 324 blue"
    "TbaCxIJ_VP4 60 yellow"
    "t6V9i8fFADI 240 magenta"
  )
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/list.txt"
  i=0
  for clip in "${clips[@]}"; do
    read -r id start colour <<< "$clip"
    src="$mme/videos/data/$id.mp4"
    part="$tmp/part$i.mp4"
    if [[ -f "$src" ]]; then
      ffmpeg -v error -y -ss "$start" -i "$src" -t 4 -an \
        -vf "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,fps=25" \
        -c:v libx264 -pix_fmt yuv420p -frames:v 100 "$part"
    else
      ffmpeg -v error -y -f lavfi -i "color=c=$colour:size=640x360:rate=25" \
        -c:v libx264 -pix_fmt yuv420p -frames:v 100 "$part"
    fi
    echo "file '$part'" >> "$tmp/list.txt"
    i=$((i + 1))
  done
  ffmpeg -v error -y -f concat -safe 0 -i "$tmp/list.txt" -c:v libx264 -pix_fmt yuv420p \
    -r 25 "$out/cuts.mp4"
  echo "wrote $out/cuts.mp4"
fi
