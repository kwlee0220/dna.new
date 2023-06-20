from __future__ import annotations

from typing import Optional
import logging

from omegaconf import OmegaConf

from dna import config, sub_logger
from dna.event import EventQueue, MultiStagePipeline


class ZonePipeline(MultiStagePipeline):
    def __init__(self, node_id:str, conf:OmegaConf,
                 *,
                 logger:Optional[logging.Logger]=None) -> None:
        super().__init__()
        
        self.node_id = node_id
        self.event_queues:dict[str,EventQueue] = dict()
        
        from .to_line_transform import ToLineTransform
        to_line = ToLineTransform(logger=sub_logger(logger, 'line'))
        self.add_stage(to_line)
        
        from .zone_event_generator import ZoneEventGenerator
        named_zones = config.get(conf, "zones", default=[])
        zone_detector = ZoneEventGenerator(named_zones, logger=sub_logger(logger, 'zone_gen'))
        self.add_stage(zone_detector)
        
        from .zone_event_refiner import ZoneEventRefiner
        event_refiner = ZoneEventRefiner(logger=sub_logger(logger, 'zone_refine'))
        self.add_stage(event_refiner)
        self.event_queues['location_changes'] = event_refiner.location_event_queue
        
        from .resident_changes import ResidentChanges
        self.resident_changes = ResidentChanges()
        self.add_listener(self.resident_changes)
        self.event_queues['resident_changes'] = self.resident_changes
        
        from .zone_sequence_collector import ZoneSequenceCollector
        collector = ZoneSequenceCollector()
        self.add_listener(collector)
        self.event_queues['zone_sequences'] = collector
        
        from .zone_sequence_collector import FinalZoneSequenceFilter
        last_zone_seq = FinalZoneSequenceFilter()
        collector.add_listener(last_zone_seq)
        self.event_queues['last_zone_sequences'] = last_zone_seq
        
        if motions := config.get(conf, "motions"):
            from .motion_detector import MotionDetector
            motion = MotionDetector(self.node_id, dict(motions), logger=sub_logger(logger, 'motion'))
            last_zone_seq.add_listener(motion)
            self.event_queues['motions'] = motion

    def close(self) -> None:
        for queue in self.event_queues.values():
            queue.close()
        super().close()