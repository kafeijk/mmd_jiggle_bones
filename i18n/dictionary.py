from common.i18n.dictionary import preprocess_dictionary

dictionary = {
    "zh_CN": {

    }
}

dictionary = preprocess_dictionary(dictionary)

dictionary["zh_HANS"] = dictionary["zh_CN"]
