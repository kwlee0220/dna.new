from __future__ import annotations
from collections import defaultdict

from collections.abc import Callable, Generator
import logging
from typing import Optional, Union

from dna.event import NodeTrack
from dna.event.event_processor import EventProcessor
from dna.event.track_event import NodeTrack
from dna.event.types import TimeElapsed
from dna.support.text_line_writer import TextLineWriter
            

def read_tracks_csv(track_file:str) -> Generator[NodeTrack, None, None]:
    import csv
    with open(track_file) as f:
        reader = csv.reader(f)
        for row in reader:
            yield NodeTrack.from_csv(row)


class JsonTrackEventGroupWriter(TextLineWriter):
    def __init__(self, file_path: str) -> None:
        super().__init__(file_path)

    def handle_event(self, group:list[NodeTrack]) -> None:
        for track in group:
            self.write(track.to_json() + '\n')
            
    def is_closeable_plugin(self) -> bool:
        """본 객체는 event chain에 의해 close() 메소드가 호출되기 때문에,
        plugin close가 호출되지 않아야 한다.

        Returns:
            bool: Plugin 종료시 close 호출 여부.
        """
        return False


class GroupByFrameIndex(EventProcessor):
    def __init__(self, min_frame_index_func:Callable[[],int], *, logger:Optional[logging.Logger]=None) -> None:
        EventProcessor.__init__(self)

        self.groups:dict[int,list[NodeTrack]] = defaultdict(list)  # frame index별로 TrackEvent들의 groupp
        self.min_frame_index_func = min_frame_index_func
        self.max_published_index = 0
        self.logger = logger

    def close(self) -> None:
        while self.groups:
            min_frame_index = min(self.groups.keys())
            group = self.groups.pop(min_frame_index, None)
            self._publish_event(group)
        super().close()

    # TODO: 만일 track이 delete 된 후, 한동안 물체가 검출되지 않으면
    # 이 delete event는 계속적으로 publish되지 않는 문제를 해결해야 함.
    # 궁극적으로는 이후 event가 발생되지 않아서 'handle_event' 메소드가 호출되지 않아서 발생하는 문제임.
    def handle_event(self, ev:Union[NodeTrack,TimeElapsed]) -> None:
        if isinstance(ev, NodeTrack):
            # 만일 새 TrackEvent가 이미 publish된 track event group의 frame index보다 작은 경우
            # late-arrived event 문제가 발생하여 예외를 발생시킨다.
            if ev.frame_index <= self.max_published_index:
                raise ValueError(f'A late TrackEvent: {ev}')

            group = self.groups[ev.frame_index]
            group.append(ev)

            # pending된 TrackEvent group 중에서 가장 작은 frame index를 갖는 group을 검색.
            frame_index = min(self.groups.keys())
            group = self.groups[frame_index]
            # frame_index, group = min(self.groups.items(), key=lambda t: t[0])

            # 본 GroupByFrameIndex 이전 EventProcessor들에서 pending된 TrackEvent 들 중에서
            # 가장 작은 frame index를 알아내어, 이 frame index보다 작은 값을 갖는 group의 경우에는
            # 이후 해당 group에 속하는 TrackEvent가 도착하지 않을 것이기 때문에 그 group들을 publish한다.
            min_frame_index = self.min_frame_index_func()
            if not min_frame_index:
                min_frame_index = ev.frame_index

            for idx in range(frame_index, min_frame_index):
                group = self.groups.pop(idx, None)
                if group:
                    if self.logger and self.logger.isEnabledFor(logging.DEBUG):
                        self.logger.debug(f'publish TrackEvent group: frame_index={idx}, count={len(group)}')
                    self._publish_event(group)
                    self.max_published_index = max(self.max_published_index, idx)
        else:
            print(ev)

    def __repr__(self) -> str:
        keys = list(self.groups.keys())
        range_str = f'[{keys[0]}-{keys[-1]}]' if keys else '[]'
        return f"{self.__class__.__name__}[max_published={self.max_published_index}, range={range_str}]"


