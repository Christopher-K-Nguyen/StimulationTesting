function combo_mat = getCombos(arr,num)
%% Variables
col_arr = 1:num;

%% Indices
idx_mat = nchoosek(arr,num);
[row_len,~] = size(idx_mat);
combo_mat = zeros(row_len,num);

%% Combinations
for rowNum = 1:row_len
    idx_arr = idx_mat(rowNum,col_arr);
    combo = arr(idx_arr);
    combo_mat(idx,:) = combo;
end

end