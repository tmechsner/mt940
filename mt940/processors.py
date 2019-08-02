# encoding=utf-8
import re
import calendar
import collections


def add_currency_pre_processor(currency, overwrite=True):
    def _add_currency_pre_processor(transactions, tag, tag_dict, *args):
        if 'currency' not in tag_dict or overwrite:  # pragma: no branch
            tag_dict['currency'] = currency

        return tag_dict

    return _add_currency_pre_processor


def date_fixup_pre_processor(transactions, tag, tag_dict, *args):
    """
    Replace illegal February 29, 30 dates with the last day of February.

    German banks use a variant of the 30/360 interest rate calculation,
    where each month has always 30 days even February. Python's datetime
    module won't accept such dates.
    """
    if tag_dict['month'] == '02':
        year = int(tag_dict['year'], 10)
        _, max_month_day = calendar.monthrange(year, 2)
        if int(tag_dict['day'], 10) > max_month_day:
            tag_dict['day'] = str(max_month_day)

    return tag_dict


def date_cleanup_post_processor(transactions, tag, tag_dict, result):
    for k in ('day', 'month', 'year', 'entry_day', 'entry_month'):
        result.pop(k, None)

    return result


def mBank_set_transaction_code(transactions, tag, tag_dict, *args):
    """
    mBank Collect uses transaction code 911 to distinguish icoming mass
    payments transactions, adding transaction_code may be helpful in further
    processing
    """
    tag_dict['transaction_code'] = int(
        tag_dict[tag.slug].split(';')[0].split(' ', 1)[0])

    return tag_dict


iph_id_re = re.compile(r' ID IPH: X*(?P<iph_id>\d{0,14});')


def mBank_set_iph_id(transactions, tag, tag_dict, *args):
    """
    mBank Collect uses ID IPH to distinguish between virtual accounts,
    adding iph_id may be helpful in further processing
    """
    matches = iph_id_re.search(tag_dict[tag.slug])

    if matches:  # pragma no branch
        tag_dict['iph_id'] = matches.groupdict()['iph_id']

    return tag_dict


tnr_re = re.compile(r'TNR:[ \n](?P<tnr>\d+\.\d+)',
                    flags=re.MULTILINE | re.UNICODE)


def mBank_set_tnr(transactions, tag, tag_dict, *args):
    """
    mBank Collect states TNR in transaction details as unique id for
    transactions, that may be used to identify the same transactions in
    different statement files eg. partial mt942 and full mt940
    Information about tnr uniqueness has been obtained from mBank support,
    it lacks in mt940 mBank specification.
    """

    matches = tnr_re.search(tag_dict[tag.slug])

    if matches:  # pragma no branch
        tag_dict['tnr'] = matches.groupdict()['tnr']

    return tag_dict


# https://www.db-bankline.deutsche-bank.com/download/MT940_Deutschland_Structure2002.pdf
DETAIL_KEYS = {
    '': 'transaction_code',
    '00': 'posting_text',
    '10': 'prima_nota',
    '20': 'purpose',
    '30': 'applicant_bin',
    '31': 'applicant_iban',
    '32': 'applicant_name',
    '34': 'return_debit_notes',
    '35': 'recipient_name',
    '60': 'additional_purpose',
}

# https://www.hettwer-beratung.de/sepa-spezialwissen/sepa-technische-anforderungen/sepa-gesch%C3%A4ftsvorfallcodes-gvc-mt-940/
GVC_KEYS = {
    '': 'purpose',
    'IBAN': 'gvc_applicant_iban',
    'BIC ': 'gvc_applicant_bin',
    'EREF': 'end_to_end_reference',
    'MREF': 'additional_position_reference',
    'CRED': 'applicant_creditor_id',
    'PURP': 'purpose_code',
    'SVWZ': 'purpose',
    'MDAT': 'additional_position_date',
    'ABWA': 'deviate_applicant',
    'ABWE': 'deviate_recipient',
    'SQTP': 'FRST_ONE_OFF_RECC',
    'ORCR': 'old_SEPA_CI',
    'ORMR': 'old_SEPA_additional_position_reference',
    'DDAT': 'settlement_tag',
    'KREF': 'customer_reference',
    'DEBT': 'debitor_identifier',
    'COAM': 'compensation_amount',
    'OAMT': 'original_amount',
}

# Special items occurring within the purpose for comdirect
COMDIRECT_SPECIAL_ITEMS = [
        {'indicator': 'END-TO-END-REF.:',
         'key': 'end_to_end_ref',
         'boolean': False,
         'val_in_next_item': True},
        {'indicator': 'CORE / MANDATSREF.:',
         'key': 'mandate_reference',
         'boolean': False,
         'val_in_next_item': True},
        {'indicator': 'GLÄUBIGER-ID:',
         'key': 'creditor_id',
         'boolean': False,
         'val_in_next_item': True},
        {'indicator': 'KARTENZAHLUNG',
         'key': 'card_payment',
         'boolean': True,
         'val_in_next_item': False},
        {'indicator': 'Ref. ',
         'key': 'reference',
         'boolean': False,
         'val_in_next_item': False},
]


def _parse_mt940_details(detail_str):
    result = dict.fromkeys(DETAIL_KEYS.values())

    tmp = collections.OrderedDict()
    segment = ''
    segment_type = ''

    for index, char in enumerate(detail_str):
        if char != '?':
            segment += char
            continue

        if index + 2 >= len(detail_str):
            break

        tmp[segment_type] = segment if not segment_type else segment[2:]
        segment_type = detail_str[index + 1] + detail_str[index + 2]
        segment = ''

    if segment_type:  # pragma: no branch
        tmp[segment_type] = segment if not segment_type else segment[2:]

    next_item_key = None
    for key, value in tmp.items():
        if key in DETAIL_KEYS:
            result[DETAIL_KEYS[key]] = value
        elif key == '33':
            key32 = DETAIL_KEYS['32']
            result[key32] = (result[key32] or '') + value
        elif key.startswith('2'):
            if next_item_key:
                result[next_item_key] = value
            else:
                skip_this_item = False
                for spec_item in COMDIRECT_SPECIAL_ITEMS:
                    if value.startswith(spec_item['indicator']):
                        skip_this_item = True
                        if spec_item['val_in_next_item']:
                            next_item_key = spec_item['key']
                        elif spec_item['boolean']:
                            result[spec_item['key']] = True
                        else:
                            result[spec_item['key']] = value.split(spec_item['indicator'])[1]
                        break
                if skip_this_item:
                    continue
                key20 = DETAIL_KEYS['20']
                result[key20] = (result[key20] or '') + value
        elif key in ('61', '62', '63'):
            key60 = DETAIL_KEYS['60']
            result[key60] = (result[key60] or '') + value
        next_item_key = None

    return result


def _parse_mt940_gvcodes(purpose):
    result = {}

    for key, value in GVC_KEYS.items():
        result[value] = None

    tmp = {}
    segment_type = None
    text = ''

    for index, char in enumerate(purpose):
        if char == '+' and purpose[index - 4:index] in GVC_KEYS:
            if segment_type:
                tmp[segment_type] = text[:-4]
                text = ''
            else:
                text = ''
            segment_type = purpose[index - 4:index]
        else:
            text += char

    if segment_type:  # pragma: no branch
        tmp[segment_type] = text
    else:
        tmp[''] = text  # pragma: no cover

    for key, value in tmp.items():
        result[GVC_KEYS[key]] = value

    return result


def transaction_details_post_processor(transactions, tag, tag_dict, result):
    details = tag_dict['transaction_details']
    details = ''.join(detail.strip('\n\r') for detail in details.splitlines())

    # check for e.g. 103?00...
    if re.match(r'^\d{3}\?\d{2}', details):
        result.update(_parse_mt940_details(details))

        purpose = result.get('purpose')

        if purpose and purpose[:4] in GVC_KEYS:  # pragma: no branch
            result.update(_parse_mt940_gvcodes(result['purpose']))

        del result['transaction_details']

    return result
