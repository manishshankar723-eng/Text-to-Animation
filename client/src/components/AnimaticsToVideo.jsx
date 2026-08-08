// Image to Video workflow shell: the library, or one open project.
//
// The same two-state shell Storyboard to Animatic uses, and for the same
// reason: there is only one screen to get to, and the workspace holds its own
// state. Opening a storyboard BOARD used to be a third state here; it is now
// its own workflow (see CreateAnimaticImage.jsx).
//
// The three video steps live INSIDE the workspace rather than out here, because
// a user moves between them freely (render a shot, add art, render again).
import { useEffect, useState } from "react";
import FinalVideoLibrary from "./FinalVideoLibrary.jsx";
import FinalVideoWorkspace from "./FinalVideoWorkspace.jsx";

export default function AnimaticsToVideo({ openId, onOpened }) {
  const [current, setCurrent] = useState(openId || null);

  // The animatic editor's "Make final video" navigates here with an id already
  // created; consume it so returning to the library later doesn't re-open it.
  useEffect(() => {
    if (!openId) return;
    setCurrent(openId);
    onOpened?.();
  }, [openId, onOpened]);

  if (current) {
    return (
      <FinalVideoWorkspace
        videoId={current}
        onBack={() => setCurrent(null)}
        onDeleted={() => setCurrent(null)}
      />
    );
  }

  return <FinalVideoLibrary onOpen={setCurrent} />;
}
