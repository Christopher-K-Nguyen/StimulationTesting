function arr_new = addCommas(arr)

try
    [numOfRows,numOfCols] = size(arr);
    numOfElem = numel(arr);
    if numOfElem > 1
        arr_new = cellstr2(numOfRows,numOfCols);
    else
        arr_new = '';
    end
    if numOfElem == 1 && arr == 0
        arr_new = '0';
    else
        for row_idx = 1:numOfRows
            for col_idx = 1:numOfCols
                num_idx = arr(row_idx,col_idx);
                numOfPlaces = floor(log10(num_idx));
                numOfSeps = floor(numOfPlaces/3);
                num_char = num2str(num_idx);

                num_char_reverse = reverse(num_char);
                decimalPoint_idx = strfind(num_char_reverse,'.');

                commaCount = 0;
                for commaNum = 1:numOfSeps
                    idx = 3 * commaNum + commaCount;
                    if ~isempty(decimalPoint_idx)
                        idx = idx + decimalPoint_idx;
                    end
                    idx_char = num_char_reverse(1:idx);
                    idx_after = num_char_reverse(idx+1:end);
                    num_char_reverse = strcat(idx_char,',',idx_after);
                    commaCount = commaCount + 1;
                end
                num_new = reverse(num_char_reverse);
                if numOfRows > 1 || numOfCols > 1
                    arr_new{row_idx,col_idx} = num_new;
                else
                    arr_new = num_new;
                end
            end
        end
    end
catch
    arr_new = num2str(arr);
end

end