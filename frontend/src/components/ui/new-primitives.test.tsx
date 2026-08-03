import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Card, CardHeader, CardTitle, CardContent } from "./card";
import { Progress } from "./progress";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./tabs";

describe("new ui primitives", () => {
  it("Card composes header, title and content", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Overview</CardTitle>
        </CardHeader>
        <CardContent>body</CardContent>
      </Card>,
    );
    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByText("body")).toBeInTheDocument();
  });

  it("Progress reflects a clamped value", () => {
    render(<Progress value={150} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "100");
  });

  it("Tabs switches the visible panel on trigger click", () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">panel-a</TabsContent>
        <TabsContent value="b">panel-b</TabsContent>
      </Tabs>,
    );
    expect(screen.getByText("panel-a")).toBeInTheDocument();
    expect(screen.queryByText("panel-b")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "B" }));
    expect(screen.getByText("panel-b")).toBeInTheDocument();
    expect(screen.queryByText("panel-a")).not.toBeInTheDocument();
  });
});
