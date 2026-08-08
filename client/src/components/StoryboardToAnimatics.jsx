// Storyboard → Animatic workflow shell: the library, or one open animatic.
//
// Deliberately a two-state shell rather than the multi-step machine the
// storyboard workflow needs — there is only one screen to get to, and the
// editor holds all of its own state.
import { useEffect, useState } from "react";
import AnimaticEditor from "./AnimaticEditor.jsx";
import AnimaticLibrary from "./AnimaticLibrary.jsx";

export default function StoryboardToAnimatics({ openId, onOpened, onMakeFinalVideo }) {
  const [current, setCurrent] = useState(openId || null);

  // The board's "Make animatic" button navigates here with an id already
  // created; consume it so returning to the library later doesn't re-open it.
  useEffect(() => {
    if (!openId) return;
    setCurrent(openId);
    onOpened?.();
  }, [openId, onOpened]);

  if (current) {
    return (
      <AnimaticEditor
        animaticId={current}
        onBack={() => setCurrent(null)}
        onDeleted={() => setCurrent(null)}
        onMakeFinalVideo={onMakeFinalVideo}
      />
    );
  }
  return <AnimaticLibrary onOpen={setCurrent} />;
}
