from __future__ import annotations

from typing import Union
import logging

from dna.event import EventProcessor, NodeTrack
from dna.node.zone import ZoneEvent, ZoneVisit, ZoneSequence

LOGGER = logging.getLogger('dna.node.zone.Turn')


class ZoneSequenceCollector(EventProcessor):
    def __init__(self) -> None:
        super().__init__()
        
        self.sequences:dict[str,ZoneSequence] = dict()
    
    def close(self) -> None:
        self.sequences.clear()
        super().close()

    def handle_event(self, ev:Union[ZoneEvent,NodeTrack]) -> None:
        if isinstance(ev, ZoneEvent):
            self.handle_zone_event(ev)
        elif isinstance(ev, NodeTrack) and ev.is_deleted():
            self.sequences.pop(ev.track_id, None)
            self._publish_event(ev)
            
    def handle_zone_event(self, ev:ZoneEvent) -> None:
        if ev.is_inside() or ev.is_unassigned():
            return
        
        seq = self.sequences.get(ev.track_id)
        if seq is None:
            seq = ZoneSequence(node_id=ev.node_id, track_id=ev.track_id, visits=[], source=ev.source)
            self.sequences[ev.track_id] = seq
            
        if ev.is_entered():
            seq.append(ZoneVisit.open(ev))
        elif ev.is_left():
            last:ZoneVisit = seq[-1]
            assert last.is_open()
            last.close(frame_index=ev.frame_index, ts=ev.ts)
        elif ev.is_through():
            last = seq[-1] if len(seq) > 0 else None
            assert last is None or last.is_closed()

            last = ZoneVisit.open(ev)
            seq.append(last)
            self._publish_event(seq.duplicate())
            
            last.close_at_event(ev)
        self._publish_event(seq.duplicate())
        
    def __repr__(self) -> str:
        return f"CollectZoneSeqs"


class FinalZoneSequenceFilter(EventProcessor):
    def __init__(self) -> None:
        super().__init__()
        
        self.sequences:dict[str,ZoneSequence] = dict()
    
    def close(self) -> None:
        self.sequences.clear()
        super().close()
        
    def handle_event(self, ev:Union[ZoneSequence,NodeTrack]) -> None:
        if isinstance(ev, ZoneSequence):
            self.sequences[ev.track_id] = ev
        elif isinstance(ev, NodeTrack) and ev.is_deleted():
            zseq = self.sequences.pop(ev.track_id, None)
            if zseq:
                self._publish_event(zseq)