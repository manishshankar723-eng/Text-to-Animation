// Image to Video workflow shell: the library, or one open project.
//
// The same two-state shell Storyboard to Animatic uses, and for the same
// reason: there is only one screen to get to, and the workspace holds its own
// state. Opening a storyboard BOARD used to be a third state here; it is now
// its own workflow (see CreateAnimaticImage.jsx).
//
// The three video steps live INSIDE the workspace rather than out here, because
// a user moves between them freely (render a shot, add art, render again).
import { useState } from "react";
import FinalVideoLibrary from "./FinalVideoLibrary.jsx";
import FinalVideoWorkspace from "./FinalVideoWorkspace.jsx";

// Nothing hands this workflow an id from outside any more — a project is always
// started from its own library — so it opens on the library and nowhere else.
export default function AnimaticsToVideo() {
  const [current, setCurrent] = useState(null);

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
