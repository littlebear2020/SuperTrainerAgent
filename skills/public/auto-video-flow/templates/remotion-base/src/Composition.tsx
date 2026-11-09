import { AbsoluteFill } from "remotion";

export const MyComposition: React.FC = () => {
  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0f172a",
        color: "white",
        justifyContent: "center",
        alignItems: "center",
        fontSize: 48,
      }}
    >
      Remotion Video Template
    </AbsoluteFill>
  );
};
