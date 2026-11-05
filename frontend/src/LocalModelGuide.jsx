import React from 'react';

export default function LocalModelGuide({ model, inputMode = 'text', continuityMode = 'independent' }) {
  const wan = model === 'wan2.2-animate-2';
  const hybrid = model === 'ltx-wan-hybrid';
  return <aside className="local-model-guide" aria-label="Model input guide">
    <strong>{wan ? 'Wan: animate one character' : hybrid ? 'Hybrid: cinematic motion with a recurring character' : 'LTX: start with a story'}</strong>
    {wan ? <><p>Required: one character image and a driving-motion MP4 for every shot. Each shot must contain exactly one character. The driving clip supplies gestures and body movement; it should cover the shot duration.</p><p>Wan creates silent animation. Choose a local narrator or fixed character voices during assembly. Fixed voices use the synthetic eSpeak baseline; neither option promises lip sync. Use LTX or hybrid for connected frame conditioning.</p></>
      : hybrid ? <><p>Required: an identity image for each character used in a single-character shot. LTX creates motion and audio; Wan establishes identity using that motion on single-character cuts. Anchored and independent modes apply Wan to every single-character shot. No driving-video upload is needed.</p>{continuityMode === 'connected' && <p>In connected mode, Wan establishes identity on camera cuts. LTX then carries continuous action from the selected preceding clip; a second Wan pass can reset the framing. Each generation quote records these stages.</p>}<p>Establishing and multi-character shots use LTX only. Use separate close-up cuts for Maya and Theo when you want Wan to animate each actor.</p></>
      : <><p>No character upload is required for independent text-to-video. Anchored and connected modes need an approved cast package. Describe each actor, outfit, location and voice in your story. A written character description helps planning but does not guarantee the same face across shots.</p><p>For stronger visual guidance, choose “Start from images” and provide a composed scene-start image for every shot. A portrait alone does not describe the whole scene.</p></>}
    {!wan && inputMode === 'image' && <p><strong>Image mode:</strong> select a shared scene-start image or assign one per scene/cut after the screenplay is ready. Connected action shots inherit the selected previous clip’s frames. Hybrid identity images and scene-start images have different roles.</p>}
    <p>After drafting, copy the exact character IDs shown in the screenplay when saving identity images. Check faces, clothing, voices and captions before approving.</p>
  </aside>;
}
