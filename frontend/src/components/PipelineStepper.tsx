"use client";

import React from "react";
import { STAGE_NAMES } from "../lib/parser";

interface PipelineStepperProps {
  currentStageIndex: number;
  selectedStageIndex: number;
  onSelectStage: (index: number) => void;
  followLive: boolean;
  onToggleFollowLive: (follow: boolean) => void;
}

// Stages 3 (Research), 4 (Stock Rank), 6 (Global Rank) are DeepSeek LLM powered by Featherless.ai.
// Others are deterministic quantitative or risk gates.
const IS_LLM_STAGE = (index: number) => index === 2 || index === 3 || index === 5;

export const PipelineStepper: React.FC<PipelineStepperProps> = ({
  currentStageIndex,
  selectedStageIndex,
  onSelectStage,
  followLive,
  onToggleFollowLive,
}) => {
  return (
    <div className="stepper-container">
      <div className="stepper-header">
        <div style={{ display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap" }}>
          <span style={{ fontWeight: 700, fontSize: "14px", color: "var(--md-sys-color-on-surface)" }}>
            Autonomous Trading Pipeline
          </span>
          <div className="stepper-legend">
            <span className="legend-item deterministic">
              <span className="legend-dot deterministic" />
              <span>Deterministic Gate</span>
            </span>
            <span className="legend-item llm">
              <span className="legend-dot llm" />
              <span>✨ Powered by Featherless.ai</span>
            </span>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              cursor: "pointer",
              fontSize: "12px",
              color: "var(--md-sys-color-on-surface-variant)",
            }}
          >
            <input
              type="checkbox"
              checked={followLive}
              onChange={(e) => onToggleFollowLive(e.target.checked)}
              style={{
                width: "16px",
                height: "16px",
                accentColor: "var(--md-sys-color-primary)",
                cursor: "pointer",
              }}
            />
            <span>Follow Live Agent</span>
          </label>
        </div>
      </div>

      <div className="stepper-track">
        {STAGE_NAMES.map((name, index) => {
          const isActive = index === selectedStageIndex;
          const isCompleted = index < selectedStageIndex;
          const isCurrentView = index === selectedStageIndex;
          const isLlm = IS_LLM_STAGE(index);
          const label = name.split(". ")[1];

          return (
            <button
              key={`${name}-${index}`}
              className={`step-pill ${isLlm ? "m3-tertiary-step" : "m3-secondary-step"} ${isActive ? "active" : ""} ${isCompleted ? "completed" : ""}`}
              onClick={() => {
                onToggleFollowLive(false);
                onSelectStage(index);
              }}
              title={`Stage ${index + 1}: ${name} (${isLlm ? "Powered by Featherless.ai" : "Deterministic Engine"})`}
            >
              <span className="step-badge">{isCompleted ? "✓" : index + 1}</span>
              <span>{label}</span>
              {isCurrentView && (
                <span
                  style={{
                    width: "6px",
                    height: "6px",
                    borderRadius: "50%",
                    backgroundColor: "var(--md-sys-color-primary)",
                    display: "inline-block",
                  }}
                />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
};
