function isSet = setRectPattern(channel_arr,pattern,rectType)
%% Variable
pattern_arr = [ ...
    pattern.A1 ...
    pattern.A2 ...
    pattern.W1 ...
    pattern.W2 ...
    pattern.Delay];
numOfChannels = length(channel_arr);
isSet = true;

if numOfChannels == 0
    numOfFiles = 1;
else
    numOfFiles = numOfChannels;
end
%% Write to Pattern File
if numOfFiles > 1
    fprintf('Generating rectangular...');
else
    fprintf('rectangular...');
end

startTime = tic;
for channel_idx = 1:numOfFiles
    if numOfChannels > 0
        channelNum = channel_arr(channel_idx);
        fprintf('%d...',channelNum);
    end
%     PS_SetPatternType(1,channelNum,1);
    PS_SetPatternType(1,channelNum,0);
    isPatternSame = false;
    startTime = tic;
    while ~isPatternSame
        switch rectType
            case 1
                PS_SetRectParam(1,channelNum,pattern_arr);
                [pattern_check,~] = PS_GetRectParam(1,channelNum);
                isPatternSame = isequal(pattern_check,pattern_arr);
                if ~isPatternSame
                    fprintf('\n');
                    param_table = table( ...
                        pattern_arr,pattern_check, ...
                        'VariableNames',{'expected','actual'});
                    display(param_table);
                    error('Patterns not matching!');
                end
            case 2
                PS_SetRectParam2(1,channelNum,pattern);
                [pattern_check,~] = PS_GetRectParam2(1,channelNum);
                isPatternSame = ...
                    pattern_check.Amp1 == pattern.A1 && ...
                    pattern_check.Amp2 == pattern.A2 && ...
                    pattern_check.W1 == pattern.W1 && ...
                    pattern_check.W2 == pattern.W2 && ...
                    pattern_check.Delay == pattern.Delay;
                if ~isPatternSame
                    fprintf('\n');
                    expected_arr = [ ...
                        pattern.A1; ...
                        pattern.A2; ...
                        pattern.W1; ...
                        pattern.W2; ...
                        pattern.Delay];
                    actual_arr = [ ...
                        pattern_check.Amp1; ...
                        pattern_check.Amp2; ...
                        pattern_check.W1; ...
                        pattern_check.W2; ...
                        pattern_check.Delay];
                    param_table = table( ...
                        expected_arr,actual_arr, ...
                        'VariableNames',{'expected','actual'});
                    display(param_table);
                    error('Patterns not matching!');
                end
        end
        if toc(startTime) > 3
            isSet = false;
            break;
        end
    end
    if ~isSet
        break;
    end
end
[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

end