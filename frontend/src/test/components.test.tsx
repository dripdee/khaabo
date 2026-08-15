/**
 * Component tests for the honesty-critical UI primitives.
 *
 * These lock the behaviours the product depends on: a null score must never render
 * as a number, an absent trend must render nothing at all, and the "Why?" text must
 * be exactly what the server sent.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NotEnoughData, WhyChips } from "@/components/Badge";
import { Score, ScorePill } from "@/components/Score";
import { Trend } from "@/components/Trend";

describe("Score", () => {
  it("renders the rounded value when present", () => {
    render(<Score value={88.4} />);
    expect(screen.getByText("88")).toBeInTheDocument();
  });

  it("renders an em dash rather than a zero when the value is null", () => {
    render(<Score value={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("labels the unranked case for assistive tech", () => {
    render(<Score value={undefined} />);
    expect(screen.getByRole("img")).toHaveAccessibleName(/not enough data/i);
  });

  it("describes the score for assistive tech", () => {
    render(<Score value={91} />);
    expect(screen.getByRole("img")).toHaveAccessibleName(/91 out of 100/i);
  });
});

describe("ScorePill", () => {
  it("says 'Not enough data' instead of a number when unranked", () => {
    render(<ScorePill value={null} />);
    expect(screen.getByText(/not enough data/i)).toBeInTheDocument();
  });

  it("shows the number when ranked", () => {
    render(<ScorePill value={73.6} />);
    expect(screen.getByText("74")).toBeInTheDocument();
  });
});

describe("Trend", () => {
  it("renders nothing when direction is null", () => {
    const { container } = render(<Trend trend={{ direction: null }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when no trend is supplied at all", () => {
    const { container } = render(<Trend trend={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a rising arrow with an accessible label", () => {
    render(<Trend trend={{ direction: "rising", delta: 0.12, significant: true }} showLabel />);
    expect(screen.getByText("Rising")).toBeInTheDocument();
    expect(screen.getByText("↑")).toBeInTheDocument();
  });

  it("does not rely on colour alone for declining", () => {
    render(<Trend trend={{ direction: "declining", delta: -0.2, significant: true }} />);
    expect(screen.getByText("Declining")).toBeInTheDocument();
  });
});

describe("WhyChips", () => {
  it("renders server-provided labels verbatim", () => {
    render(
      <WhyChips
        why={[
          { code: "positive_ratio", label: "91% positive dish sentiment", value: 0.91 },
          { code: "mentions", label: "42 dish mentions", value: 42 },
        ]}
      />,
    );
    expect(screen.getByText("91% positive dish sentiment")).toBeInTheDocument();
    expect(screen.getByText("42 dish mentions")).toBeInTheDocument();
  });

  it("renders nothing for an empty reason list", () => {
    const { container } = render(<WhyChips why={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("NotEnoughData", () => {
  it("states the limitation plainly", () => {
    render(<NotEnoughData detail="Fewer than 3 mentions" />);
    const element = screen.getByText(/not enough data/i);
    expect(element).toBeInTheDocument();
    expect(element).toHaveAttribute("title", "Fewer than 3 mentions");
  });
});
