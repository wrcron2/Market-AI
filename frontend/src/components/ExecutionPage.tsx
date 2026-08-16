import { PageHead, SectionHead } from './ui/primitives'
import { BrainActivityFeed, type BrainEvent } from './BrainActivityFeed'
import { PipelinePanel } from './PipelinePanel'
import { AuditLog } from './AuditLog'
import { VersionsPanel } from './VersionsPanel'

interface Props {
  brainEvents: BrainEvent[]
}

/** Execution — brain feed, pipeline, audit trail, and deploy controls, stacked. */
export function ExecutionPage({ brainEvents }: Props) {
  return (
    <div className="flex flex-col gap-[22px]">
      <PageHead eyebrow="Execution" title="Brain, pipeline, audit, deploy" />

      <SectionHead eyebrow="Brain activity" note="live · every pipeline step with its outcome" />
      <BrainActivityFeed liveEvents={brainEvents} />

      <div className="mf-hairline" />
      <SectionHead eyebrow="Pipeline" note="scout → debate → risk → stage" />
      <PipelinePanel />

      <div className="mf-hairline" />
      <SectionHead eyebrow="Audit log" note="every mutation, timestamped" />
      <AuditLog />

      <div className="mf-hairline" />
      <SectionHead eyebrow="Versions & deploy" note="Oracle host · docker tags" />
      <VersionsPanel />
    </div>
  )
}
