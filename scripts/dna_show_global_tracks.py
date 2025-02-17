from __future__ import annotations

from typing import Union, Optional
from contextlib import closing
from collections import defaultdict
from dataclasses import dataclass
import time
from pathlib import Path

import numpy as np
import cv2
import heapq
from omegaconf import OmegaConf
from kafka import KafkaConsumer

from dna import Box, Image, color, Frame, Point, TrackletId, initialize_logger, config, Size2d
from dna.camera import Camera
from dna.camera.video_writer import VideoWriter
from dna.event import NodeTrack, read_topics
from dna.node import stabilizer
from dna.node.world_coord_localizer import WorldCoordinateLocalizer, ContactPointType
from dna.support import plot_utils
from dna.track import TrackState
from dna.assoc import GlobalTrack
from scripts import update_namespace_with_environ

COLORS = {
    'etri:04': color.BLUE,
    'etri:05': color.GREEN,
    'etri:06': color.YELLOW,
    # 'etri:07': color.INDIGO,
    'etri:07': color.LAVENDER,
    'global': color.RED
}

RADIUS_GLOBAL = 15
RADIUS_LOCAL = 7

import argparse
def parse_args():
    parser = argparse.ArgumentParser(description="show target locations")
    parser.add_argument("--kafka_brokers", nargs='+', metavar="hosts", default=['localhost:9092'], help="Kafka broker hosts list")
    parser.add_argument("--kafka_offset", default='earliest', help="A policy for resetting offsets: 'latest', 'earliest', 'none'")
    parser.add_argument("--topic", metavar="name", default='global-tracks-overlap', help="topic name for input global tracks")
    parser.add_argument("--sync", action='store_true', help="sync to camera fps")
    parser.add_argument("--stop_on_poll_timeout", action='store_true', help="stop when a poll timeout expires")
    parser.add_argument("--timeout_ms", metavar="milli-seconds", type=int, default=1000, help="Kafka poll timeout in milli-seconds")
    parser.add_argument("--initial_timeout_ms", metavar="milli-seconds", type=int, default=5000, help="initial Kafka poll timeout in milli-seconds")
    parser.add_argument("--output_video", metavar="path", help="output video file path")
    
    parser.add_argument("--logger", metavar="file path", help="logger configuration file path")
    return parser.parse_known_args()


class GlobalTrackDrawer:
    def __init__(self, title:str, localizer:WorldCoordinateLocalizer, world_image:Image,
                 *,
                 output_video:str=None) -> None:
        self.title = title
        self.localizer = localizer
        self.world_image = world_image
        cv2.namedWindow(self.title)
        
        if output_video:
            self.writer = VideoWriter(Path(output_video).resolve(), 10, Size2d.from_image(world_image))
            self.writer.open()
        else:
            self.writer = None
        
    def close(self) -> None:
        if self.writer:
            self.writer.close()
        cv2.destroyWindow(self.title)
    
    def draw_tracks(self, gtracks:list[GlobalTrack]) -> Image:
        convas = self.world_image.copy()
        
        ts = max((gl.ts for gl in gtracks), default=None)
        convas = cv2.putText(convas, f'ts={ts}',
                            (10, 20), cv2.FONT_HERSHEY_COMPLEX_SMALL, 1, color.RED, 2)
    
        for gtrack in gtracks:
            gloc = self.to_image_coord(gtrack.location)
            
            if gtrack.is_associated():
                convas = cv2.circle(convas, gloc, radius=RADIUS_GLOBAL, color=color.RED, thickness=-1, lineType=cv2.LINE_AA)
                for ltrack in gtrack.supports:
                    track_color = COLORS[ltrack.node]
                    sample = self.to_image_coord(ltrack.location)
                    convas = cv2.line(convas, gloc, sample, track_color, thickness=1, lineType=cv2.LINE_AA)
                    convas = cv2.circle(convas, sample, radius=RADIUS_LOCAL, color=track_color, thickness=-1, lineType=cv2.LINE_AA)
            else:
                node = TrackletId.from_string(gtrack.id).node_id
                track_color = COLORS[node]
                convas = cv2.circle(convas, gloc, radius=RADIUS_LOCAL, color=track_color, thickness=-1, lineType=cv2.LINE_AA)
                
            label_pos = Point(gloc) + [-35, 30]
            convas = plot_utils.draw_label(convas, f'{gtrack.id}', label_pos.to_rint(), font_scale=0.5,
                                       color=color.BLACK, fill_color=color.YELLOW, thickness=1)
            
        # convas = ROI.crop(convas)
        if self.writer:
            self.writer.write(convas)
        cv2.imshow(self.title, convas)
        
        return convas
        
    def to_image_coord(self, world_coord:Point) -> tuple[float,float]:
        pt_m = self.localizer.from_world_coord(world_coord.xy)
        return tuple(Point(self.localizer.to_image_coord(pt_m)).to_rint().xy)


def main():
    args, _ = parse_args()
    initialize_logger(args.logger)
    args = update_namespace_with_environ(args)
    
    world_image = cv2.imread("regions/etri_testbed/ETRI_221011.png", cv2.IMREAD_COLOR)
    localizer = WorldCoordinateLocalizer('regions/etri_testbed/etri_testbed.json',
                                         camera_index=0, contact_point=ContactPointType.Simulation)
    drawer = GlobalTrackDrawer(title="Multiple Objects Tracking", localizer=localizer, world_image=world_image,
                                output_video=args.output_video)
    
    consumer = KafkaConsumer(bootstrap_servers=args.kafka_brokers,
                            #  group_id='draw_global_tracks',
                             enable_auto_commit=False,
                             auto_offset_reset=args.kafka_offset,
                             key_deserializer=lambda k: k.decode('utf-8'))
    consumer.subscribe([args.topic])
    
    with closing(consumer) as consumer:
        done = False
        last_ts = -1
        tracks:list[GlobalTrack] = []
        heap:list[GlobalTrack] = []
        
        while not done:
            for record in read_topics(consumer, initial_timeout_ms=args.initial_timeout_ms,
                                      timeout_ms=args.timeout_ms,
                                      stop_on_poll_timeout=args.stop_on_poll_timeout):
                track = GlobalTrack.deserialize(record.value)
                
                heapq.heappush(heap, track)
                if len(heap) < 30:
                    continue
                track = heapq.heappop(heap)
                
                if track.is_deleted():
                    continue
                
                if last_ts < 0:
                    last_ts = track.ts
                    
                if last_ts != track.ts:
                    drawer.draw_tracks(tracks)
                    
                    delay_ms = track.ts - last_ts if args.sync else 1
                    if delay_ms <= 0:
                        delay_ms = 1
                    elif delay_ms >= 5000:
                        delay_ms = 100
                    key = cv2.waitKey(delay_ms) & 0xFF
                    if key == ord('q'):
                        done = True
                        break
                        
                    tracks.clear()
                    last_ts = track.ts
                    
                tracks.append(track)
            
            while len(heap) > 0:
                track = heapq.heappop(heap)
                if track.is_deleted():
                    continue
                    
                if last_ts != track.ts:
                    drawer.draw_tracks(tracks)
                    
                    delay_ms = track.ts - last_ts if args.sync else 1
                    if delay_ms <= 0:
                        delay_ms = 1
                    elif delay_ms >= 5000:
                        delay_ms = 100
                    key = cv2.waitKey(delay_ms) & 0xFF
                    if key == ord('q'):
                        done = True
                        break
                        
                    tracks.clear()
                    last_ts = track.ts
                tracks.append(track)
            break
        drawer.close()

if __name__ == '__main__':
    main()