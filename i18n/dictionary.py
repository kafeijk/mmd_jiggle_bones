from common.i18n.dictionary import preprocess_dictionary

dictionary = {
    "zh_CN": {
        ("*", "Model directory not found!"): "模型目录不存在！",
        ("*", "Invalid root directory! Change to subfolder."): "模型目录为盘符根目录，请更换为其它目录！",
        ("*", "Actual files to process: {}. Total files: {}, skipped: {}"): "实际待处理数量：{}。文件总数：{}，跳过数量：{}",
    }
}

dictionary = preprocess_dictionary(dictionary)

dictionary["zh_HANS"] = dictionary["zh_CN"]
