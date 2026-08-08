// Image to Video workflow shell: the library, one open project, or one open
// storyboard board.
//
// The board is the THIRD state because "Create Animatic Image" opens the board
// page itself — the same last page Script to Storyboard ends on, where panels
// are drawn, restyled and exported. It is mounted here rather than copied, so
// there is one board screen in the app and it behaves identically wherever it
// is reached from.
//
// The three video steps live INSIDE the workspace rather than out here, because
// a user moves between them freely (render a shot, add art, render again).
import { useEffect, useState } from "react";
import FinalVideoLibrary from "./FinalVideoLibrary.jsx";
import FinalVideoWorkspace from "./FinalVideoWorkspace.jsx";
import StoryboardBoard, { styleLabelFor } from "./StoryboardBoard.jsx";

export default function AnimaticsToVideo({ openId, onOpened, onOpenAnimatic }) {
  const [current, setCurrent] = useState(openId || null);
  // The storyboard summary being viewed, or null. Held whole (not just the id)
  // so the board gets its style and aspect without a second fetch.
  const [board, setBoard] = useState(null);

  // The animatic editor's "Make final video" navigates here with an id already
  // created; consume it so returning to the library later doesn't re-open it.
  useEffect(() => {
    if (!openId) return;
    setCurrent(openId);
    setBoard(null);
    onOpened?.();
  }, [openId, onOpened]);

  if (board) {
    return (
      <StoryboardBoard
        jobId={board.job_id}
        styleLabel={styleLabelFor(board.style)}
        aspect={board.aspect_ratio || "16:9"}
        backLabel="← Your Final Videos"
        onBack={() => setBoard(null)}
        onRestart={() => setBoard(null)}
        onOpenAnimatic={onOpenAnimatic}
      />
    );
  }

  if (current) {
    return (
      <FinalVideoWorkspace
        videoId={current}
        onBack={() => setCurrent(null)}
        onDeleted={() => setCurrent(null)}
      />
    );
  }

  return <FinalVideoLibrary onOpen={setCurrent} onOpenBoard={setBoard} />;
}
