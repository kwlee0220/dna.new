from __future__ import annotations

from typing import Optional, Union
from collections.abc import Iterable
import logging

from dna import TrackId
from dna.event import EventQueue, EventProcessor, NodeTrack
from .types import ZoneRelation, ZoneEvent, LocationChanged


class TrackLocations:
    __slots__ = ('zones', 'frame_index', 'ts')
    
    def __init__(self, zones:Iterable[str], frame_index:int, ts:int) -> None:
        self.zones = set(zones)
        self.frame_index = frame_index
        self.ts = ts

    def __repr__(self) -> str:
        return f'zones={self.zones}, frame={self.frame_index}'


class ZoneEventRefiner(EventProcessor):
    __slots__ = ('locations', 'location_event_queue', 'logger')

    def __init__(self, *, logger:Optional[logging.Logger]=None) -> None:
        EventProcessor.__init__(self)

        self.locations:dict[TrackId,TrackLocations] = dict()
        self.location_event_queue = EventQueue()
        self.logger = logger

    def close(self) -> None:
        self.location_event_queue.close()
        super().close()
                
    def handle_event(self, ev:Union[ZoneEvent,NodeTrack]) -> None:
        if isinstance(ev, ZoneEvent):
            self.handle_zone_event(ev)
        elif isinstance(ev, NodeTrack) and ev.is_deleted():
            self.handle_track_deleted(ev)
        else:
            if self.logger and self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(f'unknown event: {ev}')
        
    def handle_zone_event(self, zone_ev:ZoneEvent) -> None:
        track_locations = self.locations.get(zone_ev.track_id)
        located_zones = track_locations.zones if track_locations else set()
        
        if zone_ev.relation == ZoneRelation.Unassigned:
            if located_zones:
                # 이전에 소속되었던 모든 zone에 대해 Left event를 발생시킨다.
                for zid in located_zones:
                    if self.logger and self.logger.isEnabledFor(logging.DEBUG):
                        self.logger.debug(f'generate a LEFT(track={zone_ev.track_id}, zone={zone_ev.zone_id}, frame={zone_ev.frame_index})')
                    self.publish_left(zone_ev, zone_id=zid) 
                self.update_zone_locations(track_id=zone_ev.track_id,
                                           track_locs=TrackLocations([], zone_ev.frame_index, zone_ev.ts))
            self._publish_event(zone_ev)
        elif zone_ev.relation == ZoneRelation.Left:
            # 추적 물체가 해당 zone에 포함되지 않은 상태면, 먼저 해당 물체를 zone 안에 넣는 event를 추가한다.
            if zone_ev.zone_id not in located_zones:
                if self.logger and self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug(f'generate a ENTERED(track={zone_ev.track_id}, zone={zone_ev.zone_id}, frame={zone_ev.frame_index})')
                self.publish_entered(zone_ev)
                self.enter_zone(zone_ev)
            self._publish_event(zone_ev)
            self.leave_zone(zone_ev)
        elif zone_ev.relation == ZoneRelation.Entered:
            if zone_ev.zone_id not in located_zones:
                self._publish_event(zone_ev)
                self.enter_zone(zone_ev)
            else:
                if self.logger and self.logger.isEnabledFor(logging.WARN):
                    self.logger.warn(f'ignore a ENTERED(track={zone_ev.track_id}, zone={zone_ev.zone_id}, frame={zone_ev.frame_index})')
        elif zone_ev.relation == ZoneRelation.Inside:
            if zone_ev.zone_id not in located_zones:
                # 첫번째 등장할 때 이미 한 zone에 있는 상태에서, 다음 frame에서도 동일 zone 안에 있으면
                # 해당 물체의 첫번째 zone event가 Inside가 된다. 이때는 enter event를 먼저 발생시킨다.
                if self.logger and self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug(f'generate a ENTERED(track={zone_ev.track_id}, zone={zone_ev.zone_id}, frame={zone_ev.frame_index})')
                self.publish_entered(zone_ev)
                self.enter_zone(zone_ev)
            self._publish_event(zone_ev)
        elif zone_ev.relation == ZoneRelation.Through:
            if zone_ev.zone_id in located_zones:
                self.leave_zone(zone_ev)
                if self.logger and self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug(f'replace the THROUGH(track={zone_ev.track_id}, zone={zone_ev.zone_id}) with '
                                      f'a LEFT(track={zone_ev.track_id}, zone={zone_ev.zone_id}, frame={zone_ev.frame_index})')
                self.publish_left(zone_ev)
            else:
                self._publish_event(zone_ev) 
        else:
            raise ValueError(f'invalid ZoneEvent: {zone_ev}')
                
    def handle_track_deleted(self, ev:NodeTrack) -> None:
        # 삭제된 track의 location 정보를 삭제한다
        self.locations.pop(ev.track_id, None)
        
        # TrackEvent 이벤트를 re-publish한다
        self._publish_event(ev)

    def enter_zone(self, zone_ev:ZoneEvent) -> TrackLocations:
        track_locs = self.locations.get(zone_ev.track_id)
        if track_locs:
            track_locs.zones.add(zone_ev.zone_id)
        else:
            track_locs = TrackLocations([zone_ev.zone_id], frame_index=zone_ev.frame_index, ts=zone_ev.ts)
        self.update_zone_locations(track_id=zone_ev.track_id, track_locs=track_locs)
        
        return track_locs

    def leave_zone(self, zone_ev:ZoneEvent, zone_id:Optional[str]=None) -> TrackLocations:
        zone_id = zone_id if zone_id else zone_ev.zone_id
        track_locs = self.locations.get(zone_ev.track_id)
        if track_locs:
            track_locs.zones = track_locs.zones.difference(zone_id)
            self.update_zone_locations(track_id=zone_ev.track_id, track_locs=track_locs)
        
    def update_zone_locations(self, track_id:str, track_locs:TrackLocations) -> None:
        self.locations[track_id] = track_locs
        location_changed = LocationChanged(track_id=track_id, zone_ids=track_locs.zones,
                                           frame_index=track_locs.frame_index, ts=track_locs.ts)
        self.location_event_queue._publish_event(location_changed)
        
    def publish_entered(self, zone_ev:ZoneEvent, zone_id:Optional[str]=None) -> None:
        ev = ZoneEvent(node_id=zone_ev.node_id, track_id=zone_ev.track_id, relation=ZoneRelation.Entered,
                       zone_id=zone_id if zone_id else zone_ev.zone_id,
                       frame_index=zone_ev.frame_index, ts=zone_ev.ts, source=zone_ev.source)
        self._publish_event(ev)
        
    def publish_left(self, zone_ev:ZoneEvent, zone_id:Optional[str]=None) -> None:
        ev = ZoneEvent(node_id=zone_ev.node_id, track_id=zone_ev.track_id, relation=ZoneRelation.Left,
                       zone_id=zone_id if zone_id else zone_ev.zone_id,
                       frame_index=zone_ev.frame_index, ts=zone_ev.ts, source=zone_ev.source)
        self._publish_event(ev)
        
    def __repr__(self) -> str:
        return f"RefineZoneEvents"
    