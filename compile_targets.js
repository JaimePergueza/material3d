/**
 * compile_targets.js
 * Compila un archivo targets.mind a partir de imagenes marcadoras.
 * Usa @tensorflow/tfjs CPU backend + kernels CPU de mind-ar (sin WebGL ni tfjs-node).
 *
 * Uso:
 *   node compile_targets.js <imagen0> <imagen1> ... <salida.mind>
 */

// 1. Cargar TF.js con backend CPU puro (sin WebGL, sin tfjs-node)
import * as tf from '@tensorflow/tfjs';
import '@tensorflow/tfjs-backend-cpu';

// 2. Registrar kernels CPU personalizados de mind-ar (BinomialFilter, etc.)
import 'mind-ar/src/image-target/detector/kernels/cpu/index.js';

// 3. Canvas para leer imagenes (via @napi-rs/canvas por el override)
import { createCanvas, loadImage } from 'canvas';

// 4. Clases internas de mind-ar (sin Compiler que usa ?worker&inline)
import { CompilerBase } from 'mind-ar/src/image-target/compiler-base.js';
import { extractTrackingFeatures } from 'mind-ar/src/image-target/tracker/extract-utils.js';
import { buildTrackingImageList } from 'mind-ar/src/image-target/image-list.js';

import fs from 'fs';
import path from 'path';

// Subclase Node.js: reemplaza document.createElement y el Web Worker
class NodeCompiler extends CompilerBase {
  createProcessCanvas(img) {
    return createCanvas(img.width, img.height);
  }

  compileTrack({ progressCallback, targetImages, basePercent }) {
    return new Promise((resolve) => {
      const percentPerImage = 100.0 / targetImages.length;
      let percent = 0.0;
      const list = [];

      for (let i = 0; i < targetImages.length; i++) {
        const targetImage = targetImages[i];
        const imageList   = buildTrackingImageList(targetImage);
        const percentPerAction = percentPerImage / imageList.length;

        const trackingData = extractTrackingFeatures(imageList, () => {
          percent += percentPerAction;
          progressCallback(basePercent + percent * basePercent / 100);
        });
        list.push(trackingData);
      }
      resolve(list);
    });
  }
}

async function main() {
  // Usar backend CPU (no WebGL)
  await tf.setBackend('cpu');
  await tf.ready();

  const args = process.argv.slice(2);

  if (args.length < 2) {
    console.error('Error: se necesita al menos una imagen y una ruta de salida.');
    console.error('Uso: node compile_targets.js <img0> [img1 ...] <salida.mind>');
    process.exit(1);
  }

  const imagePaths = args.slice(0, -1);
  const outputPath  = args[args.length - 1];

  console.log(`\nCompilando ${imagePaths.length} imagen(es):`);
  imagePaths.forEach((p, i) => console.log(`  [${i}] ${path.basename(p)}`));
  console.log('');

  const compiler = new NodeCompiler();
  const images   = [];

  for (const imgPath of imagePaths) {
    const img = await loadImage(imgPath);
    images.push(img);
  }

  await compiler.compileImageTargets(images, (progress) => {
    process.stdout.write(`\rProgreso: ${progress.toFixed(1)}%   `);
  });
  console.log('\n');

  const buffer    = compiler.exportData();
  const outputDir = path.dirname(outputPath);

  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  fs.writeFileSync(outputPath, Buffer.from(buffer));
  console.log(`OK targets.mind guardado en: ${outputPath}`);
}

main().catch((err) => {
  console.error('ERROR:', err.message || err);
  process.exit(1);
});
