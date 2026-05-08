function c = cellstr2(row,col)

c = cell(row,col);

for row_idx = 1:row
    for col_idx = 1:col
        c{row_idx,col_idx} = '';
    end
end

end