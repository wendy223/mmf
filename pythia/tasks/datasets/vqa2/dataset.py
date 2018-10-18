# Copyright (c) Facebook, Inc. and its affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.
#


import numpy as np

from .utils import VocabDict
from pythia.core.constants import imdb_version
# TODO: Move in __all__ in __init__.py
from pythia.tasks.datasets.coco.coco_features_dataset \
    import COCOFeaturesDataset
from pythia.core.tasks.datasets.image_dataset import ImageDataset
from pythia.core.tasks.datasets.base_dataset import BaseDataset


def compute_answer_scores(answers, num_of_answers, unk_idx):
    scores = np.zeros((num_of_answers), np.float32)
    for answer in set(answers):
        if answer == unk_idx:
            scores[answer] = 0
        else:
            answer_count = answers.count(answer)
            scores[answer] = min(np.float32(answer_count)*0.3, 1)
    return scores


class VQA2Dataset(BaseDataset):
    def __init__(self, imdb_file,
                 image_feat_directories, verbose=False, **data_params):
        super(VQA2Dataset, self).__init__('vqa', data_params)

        if imdb_file.endswith('.npy'):
            imdb = ImageDataset(imdb_file)
        else:
            raise TypeError('unknown imdb format.')
        self.verbose = verbose
        self.imdb = imdb
        self.image_feat_directories = image_feat_directories
        self.data_params = data_params
        self.channel_first = data_params['image_depth_first']
        self.max_bboxes = (data_params['image_max_loc']
                           if 'image_max_loc' in data_params else None)
        self.vocab_dict = VocabDict(data_params['vocab_question_file'])

        # TODO: Update T_encoder and T_decoder to proper names
        self.T_encoder = data_params['T_encoder']

        # read the header of imdb file
        self.load_gt_layout = False
        data_version = self.imdb.get_version()

        if data_version != imdb_version:
            print("observed imdb_version is",
                  data_version,
                  "expected imdb version is",
                  imdb_version)
            raise TypeError('imdb version do not match.')

        if 'load_gt_layout' in data_params:
            self.load_gt_layout = data_params['load_gt_layout']
        # the answer dict is always loaded, regardless of self.load_answer
        self.answer_dict = VocabDict(data_params['vocab_answer_file'])

        if self.load_gt_layout:
            self.T_decoder = data_params['T_decoder']
            self.assembler = data_params['assembler']
            self.prune_filter_module = (data_params['prune_filter_module']
                                        if 'prune_filter_module' in data_params
                                        else False)
        else:
            print('imdb does not contain ground-truth layout')
            print('Loading model and config ...')

        self.features_db = COCOFeaturesDataset(
                            image_feat_dirs=self.image_feat_directories,
                            channel_first=self.channel_first,
                            max_bboxes=self.max_bboxes,
                            imdb=self.imdb,
                            return_info=self.load_gt_layout)

    def __len__(self):
        return len(self.imdb) - 1

    def __getitem__(self, idx):
        input_seq = np.zeros((self.T_encoder), np.int32)
        idx += self.first_element_idx
        iminfo = self.imdb[idx]['info']
        question_inds = (
            [self.vocab_dict.word2idx(w) for w in iminfo['question_tokens']])
        seq_length = len(question_inds)
        read_len = min(seq_length, self.T_encoder)
        input_seq[:read_len] = question_inds[:read_len]

        image_features = self.features_db[idx]

        answer_tokens = None
        valid_answers_idx = np.zeros((10), np.int32)
        valid_answers_idx.fill(-1)
        answer_scores = np.zeros(self.answer_dict.num_vocab, np.float32)
        if self.load_answer:
            if 'answer_tokens' in iminfo:
                answer_tokens = iminfo['answer_tokens']
            elif 'valid_answers_tokens' in iminfo:
                valid_answers_tokens = iminfo['valid_answers_tokens']
                answer_tokens = np.random.choice(valid_answers_tokens)
                valid_answers_idx[:len(valid_answers_tokens)] = (
                    [self.answer_dict.word2idx(ans)
                     for ans in valid_answers_tokens])
                ans_idx = (
                    [self.answer_dict.word2idx(ans)
                     for ans in valid_answers_tokens])
                answer_scores = (
                    compute_answer_scores(ans_idx,
                                          self.answer_dict.num_vocab,
                                          self.answer_dict.UNK_idx))

            answer_idx = self.answer_dict.word2idx(answer_tokens)

        if self.load_gt_layout:
            gt_layout_tokens = iminfo['gt_layout_tokens']
            if self.prune_filter_module:
                for n_t in range(len(gt_layout_tokens) - 1, 0, -1):
                    if (gt_layout_tokens[n_t - 1] in {'_Filter', '_Find'}
                            and gt_layout_tokens[n_t] == '_Filter'):
                        gt_layout_tokens[n_t] = None
                gt_layout_tokens = [t for t in gt_layout_tokens if t]
            gt_layout = np.array(self.assembler.module_list2tokens(
                gt_layout_tokens, self.T_decoder))

        sample = dict(input_seq_batch=input_seq,
                      seq_length_batch=seq_length)

        for idx in range(len(image_features.keys())):
            if ("image_feature_%d" % idx) in image_features:
                image_feature = image_features["image_feature_%d" % idx]
                feat_key = "image_feature_%s" % str(idx)
                sample[feat_key] = image_feature
            else:
                break

        if "image_info_0" in image_features:
            info = image_features['image_info_0']
            if "max_bboxes" in info:
                sample['image_dim'] = info['max_bboxes']

            if "bboxes" in info:
                sample['image_boxes'] = info['bboxes']

        if self.load_answer:
            sample['answer_label_batch'] = answer_idx
        if self.load_gt_layout:
            sample['gt_layout_batch'] = gt_layout

        if valid_answers_idx is not None:
            sample['valid_ans_label_batch'] = valid_answers_idx
            sample['answers'] = answer_scores

        # used for error analysis and debug,
        # output question_id, image_id, question, answer,valid_answers,
        if self.verbose:
            sample['verbose_info'] = iminfo

        return sample
