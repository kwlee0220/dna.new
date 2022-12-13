# vim: expandtab:ts=4:sw=4
from __future__ import absolute_import
from typing import List, Union, Tuple
import enum

from numpy.linalg import det
from shapely import geometry

from .detection import Detection
from . import matcher, utils
from .utils import all_indices, intersection, subtract, project, filter_overlaps
import dna
import numpy as np
from dna.types import Box, Size2d
import kalman_filter
import linear_assignment
import iou_matching
from track import Track

import logging
LOGGER = logging.getLogger('dna.tracker.deepsort')


_HOT_DIST_THRESHOLD = 21
_COST_THRESHOLD = 0.5
_COST_THRESHOLD_WEAK = 0.75
_COST_THRESHOLD_STRONG = 0.2

class Tracker:
    def __init__(self, domain, metric, params):
        self.domain = domain
        self.metric = metric
        self.params = params
        self.new_track_overlap_threshold = 0.75

        self.kf = kalman_filter.KalmanFilter()
        self.tracks:List[Track] = []
        self._next_id = 1

    def predict(self):
        """Propagate track state distributions one time step forward.

        This function should be called once every time step, before `update`.
        """
        for track in self.tracks:
            track.predict(self.kf)

    def update(self, detections):
        # Run matching cascade.
        matches, unmatched_track_idxs, unmatched_detections = self._match(detections)

        # Update locations of matched tracks
        for tidx, didx in matches:
            self.tracks[tidx].update(self.kf, detections[didx])

        # get bounding-boxes of all tracks
        t_boxes = [utils.track_to_box(track) for track in self.tracks]

        # track의 bounding-box가 exit_region에 포함된 경우는 delete시킨다.
        for tidx in range(len(self.tracks)):
            if dna.utils.find_any_centroid_cover(t_boxes[tidx], self.params.exit_zones) >= 0:
                self.tracks[tidx].mark_deleted()
        
        # unmatch된 track들 중에서 해당 box가 이미지 영역에서 1/4이상 넘어가면
        # lost된 것으로 간주한다.
        for tidx in unmatched_track_idxs:
            track:Track = self.tracks[tidx]
            if not track.is_deleted():
                ratios = t_boxes[tidx].overlap_ratios(self.domain)
                if ratios[0] < 0.85:
                    track.mark_deleted()
                else:
                    track.mark_missed()

        # confirmed track과 너무 가까운 tentative track들을 제거한다.
        # 일반적으로 이런 track들은 이전 frame에서 한 물체의 여러 detection 검출을 통해 track이 생성된
        # 경우가 많아서 이를 제거하기 위함이다.
        matcher.delete_overlapped_tentative_tracks(self.tracks, self.params.max_overlap_ratio)
        
        if len(unmatched_detections) > 0:
            new_track_idxs = unmatched_detections.copy()
            d_boxes: List[Box] = [d.bbox for d in detections]
            
            # 새로 추가될 track의 조건을 만족하지 않는 unmatched_detection들을 제거시킨다.
            for didx in unmatched_detections:
                box = d_boxes[didx]
                
                # 일정크기 이하의 unmatched_detections들은 제외시킴.
                if box.width < self.params.min_size.width or box.height < self.params.min_size.height:
                    new_track_idxs.remove(didx)
                    continue
                
                # Exit 영역에 포함되는 detection들은 무시한다
                if dna.utils.find_any_centroid_cover(box, self.params.exit_zones) >= 0:
                    new_track_idxs.remove(didx)
                    # LOGGER.debug((f"remove an unmatched detection contained in a blind region: "
                    #                 f"removed={didx}, frame={dna.DEBUG_FRAME_IDX}"))
                    continue

                # 이미 match된 detection과 겹치는 비율이 너무 크거나,
                # 다른 unmatched detection과 겹치는 비율이 크면서 그 detection 영역보다 작은 경우 제외시킴.
                for idx, ov in filter_overlaps(box, d_boxes, lambda v: max(v) >= self.new_track_overlap_threshold):
                    if idx != didx and (idx not in unmatched_detections or d_boxes[idx].area() >= box.area()):
                        LOGGER.debug((f"remove an unmatched detection that overlaps with better one: "
                                        f"removed={didx}, better={idx}, ratios={max(ov):.2f}"))
                        new_track_idxs.remove(didx)
                        break

            # 조건을 통과한 unmatched detection들은 새로 생성된 track으로 설정한다.
            for didx in new_track_idxs:
                d_box = d_boxes[didx]
                if dna.utils.find_any_centroid_cover(d_box, self.params.stable_zones) < 0:
                    track = self._initiate_track(detections[didx])
                    self.tracks.append(track)
                    self._next_id += 1
                else:
                    print("no track initiated at stable zones")

        deleted_tracks = [t for t in self.tracks if t.is_deleted() and t.age > 1]
        self.tracks = [t for t in self.tracks if not t.is_deleted()]

        # Update distance metric.
        confirmed_tracks = [t for t in self.tracks if t.is_confirmed()]
        features, targets = [], []
        for track in confirmed_tracks:
            features += track.features
            targets += [track.track_id for _ in track.features]

            # # 왜 이전 feature들을 유지하지 않지?
            track.features = [track.features[-1]] #Retain most recent feature of the track.
            # track.features = track.features[-5:]

        active_targets = [t.track_id for t in confirmed_tracks]
        self.metric.partial_fit(np.asarray(features), np.asarray(targets), active_targets)

        return deleted_tracks
                
    # 반환값
    #   * Track 작업으로 binding된 track 객체와 할당된 detection 객체: (track_idx, det_idx)
    #   * 기존 track 객체들 중에서 이번 작업에서 확인되지 못한 track 객체들의 인덱스 list
    #   * Track에 할당되지 못한 detection 객체들의 인덱스 list
    def _match(self, detections) -> Tuple[List[Tuple[int,int]],List[int],List[int]]:
        if len(detections) == 0:
            # Detection 작업에서 아무런 객체를 검출하지 못한 경우.
            return [], all_indices(self.tracks), detections

        # Split track set into confirmed and unconfirmed tracks.
        confirmed_tracks = [i for i, t in enumerate(self.tracks) if t.is_confirmed()]
        hot_tracks = [i for i, t in enumerate(self.tracks) if t.is_confirmed() and t.time_since_update <= 3]
        unconfirmed_tracks = [i for i, t in enumerate(self.tracks) if not t.is_confirmed()]

        # 이전 track 객체와 새로 detection된 객체사이의 거리 값을 기준으로 cost matrix를 생성함.
        dist_cost = self.distance_cost(self.tracks, detections)
        if dna.DEBUG_PRINT_COST:
            self.print_dist_cost(dist_cost, 999)

        matches = []
        unmatched_tracks = all_indices(self.tracks)
        unmatched_detections = all_indices(detections)

        #####################################################################################################
        ########## Hot track에 한정해서 tight한 distance 정보를 사용해서 matching 실시
        ########## Matching시 단순 distance 값만 보는 것이 아니라, 해당 detection과의 거리가
        ########## 독점적으로 가까운가도 함께 고려한다.
        #####################################################################################################
        if len(detections) > 0 and len(hot_tracks) > 0:
            matches_hot, unmatched_hot, unmatched_detections \
                = matcher.matching_by_excl_best(dist_cost, _HOT_DIST_THRESHOLD, hot_tracks, unmatched_detections)
            matches += matches_hot
            if dna.DEBUG_PRINT_COST:
                print(f"[hot, only_dist, {_HOT_DIST_THRESHOLD}]:", self.matches_str(matches_hot))
            unmatched_tracks = subtract(unmatched_tracks, project(matches_hot, 0))

            # active track과 binding된 detection과 상당히 겹치는 detection들을 제거한다.
            if len(matches_hot) > 0 and len(unmatched_detections) > 0:
                d_boxes = [det.bbox for det in detections]
                overlaps = matcher.overlap_detections(d_boxes, detections, self.params.max_overlap_ratio,
                                                        project(matches_hot, 1), unmatched_detections)
                if len(overlaps) > 0:
                    if LOGGER.isEnabledFor(logging.DEBUG):
                        str = ','.join([f"({i2}->{i1}:{s:.2f})" for i1, i2, s in overlaps])
                        LOGGER.debug((f"remove hot-track's overlaps: {str}, frame={dna.DEBUG_FRAME_IDX}"))
                    unmatched_detections = subtract(unmatched_detections, overlaps)
        else:
            unmatched_hot = hot_tracks

        #####################################################################################################
        ########## 통합 비용 행렬을 생성한다.
        ########## cmatrix: 통합 비용 행렬
        ########## ua_matrix: unconfirmed track을 고려한 통합 비용 행렬
        #####################################################################################################
        if len(unmatched_tracks) > 0 and len(unmatched_detections) > 0:
            metric_cost = self.metric_cost(self.tracks, detections)
            cmatrix, cmask = matcher.combine_cost_matrices(metric_cost, dist_cost, self.tracks, detections)
            cmatrix[cmask] = 9.99
            if dna.DEBUG_PRINT_COST:
                matcher.print_matrix(self.tracks, detections, metric_cost, 1, unmatched_tracks, unmatched_detections)
                matcher.print_matrix(self.tracks, detections, cmatrix, 9.98, unmatched_tracks, unmatched_detections)
            ua_matrix = cmatrix
        if len(unconfirmed_tracks) > 0 and len(unmatched_detections) > 0:
            hot_mask = matcher.hot_unconfirmed_mask(cmatrix, 0.1, unconfirmed_tracks, unmatched_detections)
            hot_mask = np.logical_and(hot_mask, cmatrix >= 0.2)
            ua_matrix = matcher.create_matrix(cmatrix, 9.99, hot_mask)

        #####################################################################################################
        ################ Hot track에 한정해서 강한 threshold를 사용해서  matching 실시
        ################ Tentative track에 비해 2배 이상 먼거리를 갖는 경우에는 matching을 하지 않도록 함.
        #####################################################################################################
        if len(unmatched_hot) > 0 and len(unmatched_detections) > 0:
            matrix = matcher.create_matrix(ua_matrix, _COST_THRESHOLD_STRONG)
            if dna.DEBUG_PRINT_COST:
                matcher.print_matrix(self.tracks, detections, matrix, _COST_THRESHOLD_STRONG,
                                        unmatched_hot, unmatched_detections)

            matches_s, _, unmatched_detections =\
                matcher.matching_by_hungarian(matrix, _COST_THRESHOLD_STRONG, unmatched_hot, unmatched_detections)
            if dna.DEBUG_PRINT_COST:
                print(f"[hot, tentative_aware, {_COST_THRESHOLD_STRONG}]:", self.matches_str(matches_s))
            matches += matches_s
            unmatched_tracks = subtract(unmatched_tracks, project(matches_s, 0))
        else:
            matrix = None

        #####################################################################################################
        ################ Tentative track에 한정해서 강한 threshold를 사용해서  matching 실시
        #####################################################################################################
        if len(unconfirmed_tracks) > 0 and len(unmatched_detections) > 0:
            matrix = matcher.create_matrix(ua_matrix, _COST_THRESHOLD_STRONG) if matrix is None else matrix
            matches_s, _, unmatched_detections =\
                matcher.matching_by_hungarian(matrix, _COST_THRESHOLD_STRONG, unconfirmed_tracks, unmatched_detections)
            if dna.DEBUG_PRINT_COST:
                print(f"[tentative, combined, {_COST_THRESHOLD_STRONG}]:", self.matches_str(matches_s))
            matches += matches_s
            unmatched_tracks = subtract(unmatched_tracks, project(matches_s, 0))

        #####################################################################################################
        ################ 전체 track에 대해 matching 실시
        ################ Temporarily-lost된 track들이 다시 자기의 detection과
        ################ matching되는 경우 여기서 주로 발생할 것으로 기대한다.
        #####################################################################################################
        if len(unmatched_tracks) > 0 and len(unmatched_detections) > 0:
            # Tentative track의 거리에 penalty를 주어 다른 track에 비해 덜 matching되게 한다.
            # 'ua_matrix' 대산 'cmatrix'를 사용하여 temporarily-lost track에 주어던 penalty는 제거한다.
            unconfirmed_weights = np.array([1 if track.is_confirmed() else 2 for track in self.tracks])
            weighted_matrix = np.multiply(cmatrix, unconfirmed_weights[:, np.newaxis])
            matrix = matcher.create_matrix(weighted_matrix, _COST_THRESHOLD)
            if dna.DEBUG_PRINT_COST:
                matcher.print_matrix(self.tracks, detections, matrix, _COST_THRESHOLD,
                                        unmatched_tracks, unmatched_detections)

            matches_s, unmatched_tracks, unmatched_detections =\
                matcher.matching_by_hungarian(matrix, _COST_THRESHOLD, unmatched_tracks, unmatched_detections)
            if dna.DEBUG_PRINT_COST:
                print(f"[all, combined, {_COST_THRESHOLD}]:", self.matches_str(matches_s))
            matches += matches_s

        #####################################################################################################
        ################ 겹침 정도를 고려하는 대신 조금 더 느슨한 threshold를 사용하여 matching 실시.
        #####################################################################################################
        if len(unmatched_tracks) > 0 and len(unmatched_detections) > 0:
            matrix = matcher.create_matrix(weighted_matrix, _COST_THRESHOLD_WEAK)

            # IOU 값이 0.1보다 같거나 작은 경우는 matching에서 제외시킨다.
            iou_matrix = matcher.iou_matrix(self.tracks, detections, unmatched_tracks, unmatched_detections)
            matrix[iou_matrix <= 0.1] = _COST_THRESHOLD_WEAK + 0.00001
            if dna.DEBUG_PRINT_COST:
                matcher.print_matrix(self.tracks, detections, matrix, _COST_THRESHOLD_WEAK,
                                        unmatched_tracks, unmatched_detections)

            matches_s, unmatched_tracks, unmatched_detections =\
                matcher.matching_by_hungarian(matrix, _COST_THRESHOLD_WEAK, unmatched_tracks, unmatched_detections)
            matches += matches_s
            if dna.DEBUG_PRINT_COST:
                print(f"[all, iou_weak, {_COST_THRESHOLD_WEAK}]:", self.matches_str(matches_s))

        #####################################################################################################
        ################ 남은 unmatched track에 대해서 겹침 정보를 기반으로 matching 실시
        #####################################################################################################
        if len(unmatched_tracks) > 0 and len(unmatched_detections) > 0:
            matches_s, unmatched_tracks, unmatched_detections = \
                linear_assignment.min_cost_matching(iou_matching.iou_cost, self.params.max_iou_distance,
                                                    self.tracks, detections,
                                                    unmatched_tracks, unmatched_detections)
            matches += matches_s

        return matches, unmatched_tracks, unmatched_detections

    def _initiate_track(self, detection: Detection):
        mean, covariance = self.kf.initiate(detection.bbox.to_xyah())
        return Track(mean, covariance, self._next_id, self.params.n_init, self.params.max_age, detection)

    ###############################################################################################################
    # kwlee
    def metric_cost(self, tracks, detections):
        features = np.array([det.feature for det in detections])
        targets = np.array([track.track_id for track in tracks])
        return self.metric.distance(features, targets)

    # kwlee
    def distance_cost(self, tracks, detections):
        dist_matrix = np.zeros((len(tracks), len(detections)))
        if len(tracks) > 0 and len(detections) > 0:
            measurements = np.asarray([det.bbox.to_xyah() for det in detections])
            for row, track in enumerate(tracks):
                dist_matrix[row, :] = self.kf.gating_distance(track.mean, track.covariance, measurements)
        return dist_matrix

    def matches_str(self, matches):
        return ",".join([f"({self.tracks[tidx].track_id}, {didx})" for tidx, didx in matches])

    def print_dist_cost(self, dist_cost, trim_overflow=None):
        if trim_overflow:
            dist_cost = dist_cost.copy()
            dist_cost[dist_cost > trim_overflow] = trim_overflow

        for tidx, track in enumerate(self.tracks):
            dists = [int(round(v)) for v in dist_cost[tidx]]
            track_str = f" {tidx:02d}: {track.track_id:03d}({track.state},{track.time_since_update:02d})"
            dist_str = ', '.join([f"{v:4d}" if v != trim_overflow else "    " for v in dists])
            print(f"{track_str}: {dist_str}")

    ###############################################################################################################