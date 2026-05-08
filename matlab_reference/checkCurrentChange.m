function currentStim = checkCurrentChange(currentChange_arr,currentStim,currentStim_new)
%% Variables
currentChange_len = length(currentChange_arr);
currentChange = currentChange_arr(currentChange_len);

%% Function
if currentChange_len > 4 && currentChange > 0
    currentChange1 = currentChange_arr(currentChange_len-3);
    currentChange2 = currentChange_arr(currentChange_len-2);
    currentChange3 = currentChange_arr(currentChange_len-1);
    currentChange4 = currentChange_arr(currentChange_len);
    currentChange1_sign = sign(currentChange1);
    currentChange2_sign = sign(currentChange2);
    currentChange3_sign = sign(currentChange3);
    currentChange4_sign = sign(currentChange4);
    isSkipEqual = currentChange1 == currentChange3...
        && currentChange2 == currentChange4;
    isNextOpposite = currentChange1_sign == -currentChange2_sign...
        && currentChange3_sign == -currentChange4_sign;
    if isSkipEqual && isNextOpposite
        currentChange_new = round(currentChange/2,1);
        currentStim = currentStim + currentChange_new;
    else
        currentStim = currentStim_new;
    end
else
    currentStim = currentStim_new;
end

end