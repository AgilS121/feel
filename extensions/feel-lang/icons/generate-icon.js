// Run with: node generate-icon.js
// Requires: npm install sharp (or use an online SVG-to-PNG converter)
// The marketplace requires a 128x128 PNG as icon.png

const fs = require('fs');
const path = require('path');

// If you have sharp installed:
// const sharp = require('sharp');
// sharp(path.join(__dirname, 'icon.svg'))
//   .resize(128, 128)
//   .png()
//   .toFile(path.join(__dirname, 'icon.png'))
//   .then(() => console.log('icon.png generated'))
//   .catch(err => console.error(err));

// Alternative: use https://svgtopng.com or Inkscape:
// inkscape icon.svg --export-png=icon.png --export-width=128 --export-height=128
console.log('To generate icon.png, convert icons/icon.svg to a 128x128 PNG.');
console.log('Options:');
console.log('  1. Use an online converter: https://svgtopng.com');
console.log('  2. Inkscape: inkscape icon.svg --export-png=icon.png -w 128 -h 128');
console.log('  3. ImageMagick: magick convert -background none icon.svg -resize 128x128 icon.png');
console.log('  4. npm install sharp, then run: node generate-icon.js');
