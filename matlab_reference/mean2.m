function [data_mean,data_sd] = mean2(data)

[row,~] = size(data);
data_mean = zeros(row,1);
data_sd = zeros(row,1);
for rowNum = 1:row
    arr = data(rowNum,:);
    empty_tf = arr == 0;
    arr(empty_tf) = [];
    avg = mean(arr);
    sd = std(arr);
    data_mean(rowNum) = avg;
    data_sd(rowNum) = sd;
end

end