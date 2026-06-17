# Assets

## App icon

Drop a square **`icon.png`** here (1024×1024 recommended). It's picked up automatically:

- **Dev** (`npm start`): set as the macOS Dock icon and the Windows/Linux window icon.
- **Packaged build** (later, via electron-builder): point `build.icon` at `assets/icon.icns`.

Generate the macOS `.icns` from the PNG when you're ready to package:

```bash
# from a 1024×1024 icon.png
mkdir icon.iconset
for s in 16 32 64 128 256 512; do
  sips -z $s $s   icon.png --out icon.iconset/icon_${s}x${s}.png
  sips -z $((s*2)) $((s*2)) icon.png --out icon.iconset/icon_${s}x${s}@2x.png
done
iconutil -c icns icon.iconset -o assets/icon.icns
rm -r icon.iconset
```
