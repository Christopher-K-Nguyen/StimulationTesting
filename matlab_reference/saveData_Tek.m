function [filepath_mat,filepath_csv,filepath_xlsx] = saveData_Tek(File)
%% Variables
numOfDevices = length(File.Oscilloscope);
capture_arr = [File.Data.Index];
captureNum = length(capture_arr);

hasCurrent = false;
totalChannels = 0;
for deviceNum = 1:numOfDevices
    numOfChannels = File.Oscilloscope(deviceNum).NumberOfChannels;
    channelName_cell = File.Oscilloscope(deviceNum).ChannelNames;
    if contains2(channelName_cell,'curr')
        hasCurrent = true;
    end
    totalChannels = totalChannels + numOfChannels;
end    

subject = File.Subject;
subject_fix = strrep(subject,' ','_');
savePath = File.Path;
mkdir(savePath);
name = File.Data(captureNum).Name;
name_fix = strrep(name,' ',',');

if hasCurrent
    numOfVar = totalChannels + 4;
else
    numOfVar = totalChannels + 3;
end
dataLength = File.Oscilloscope(1).Settings.DataLength;
data_cell = cell(dataLength,numOfVar);
% data_mat = zeros(dataLength,numOfChannels);
dateTime_cell = cell(dataLength,1);
tableHeadings = {};

%% File Names
% .mat filename
filename_mat = [subject_fix '.mat'];       	% filename for .mat file
filepath_mat = fullfile(savePath,filename_mat);  % save path for .mat file

% .csv filename
filename_csv = [name_fix '.csv'];           % filename for .csv file
filepath_csv = fullfile(savePath,filename_csv); 	% save path for .xlsx file

% .xlsx filename
filename_xlsx = [subject_fix '.xlsx'];           % filename for .xlsx file
filepath_xlsx = fullfile(savePath,filename_xlsx); 	% save path for .xlsx file

%% Save
% Making .mat file
fprintf('Saving MATLAB file...');
startTime = tic;
varName = getVarName(File);
newVarName = ['File_' subject_fix];
setNewVarName = sprintf('%s = %s;',newVarName,varName);
eval(setNewVarName)
save(filepath_mat,newVarName);             	% save .mat file
[endTime,unit] = getEndTime(startTime);
fprintf('"%s" (%.2f %s)\n',filename_mat,endTime,unit);         	% .mat file saved

% Making spreadsheet files
fprintf('Making spreadsheet...');
startTime = tic;

% Time
time_arr = File.Data(captureNum).Time;
time_cell = num2cell(time_arr);
data_cell(:,1) = time_cell;
tableHeadings_alloc = [tableHeadings,'Time (us)'];
tableHeadings = tableHeadings_alloc;

% Channels
data_col = 1;
for deviceNum = 1:numOfDevices
    channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
    channelName_cell = File.Oscilloscope(deviceNum).ChannelNames;
    numOfChannels = File.Oscilloscope(deviceNum).NumberOfChannels;
    for channel_idx = 1:numOfChannels
        data_col = data_col + 1;
        if numOfDevices == 2 && deviceNum == 1
            switch channel_idx
                case 1
                    channelField = 'Voltage';
                case 2
                    channelField = 'Current';
            end
        else
            channelField = channelSelect_cell{channel_idx};
        end
        channelName = channelName_cell{channel_idx};
        data_arr = File.Data(captureNum).(channelField);
    %     data_mat(:,channel_idx) = data_arr;
        if isempty(data_arr)
            data_cell(:,data_col) = cell(dataLength,1);
        else
            data_cell(:,data_col) = num2cell(data_arr);
        end
        tableHeadings_alloc = [tableHeadings,channelName];
        tableHeadings = tableHeadings_alloc;
    end
end

% Current Density
if hasCurrent
    idx_shift = 2;
    currentDensity = File.Data(captureNum).CurrentDensity;
    currentDensity_cell = num2cell(currentDensity);
    data_cell(:,totalChannels+idx_shift) = currentDensity_cell;
    tableHeadings_alloc = [tableHeadings,'Current Density A/cm2'];
    tableHeadings = tableHeadings_alloc;
else
    idx_shift = 1;
end

% Parameters
amplitude = File.Data(captureNum).Amplitude;
phaseWidth = File.Data(captureNum).PhaseWidth;
chargePhase = File.Data(captureNum).ChargePhase;
chargeInjection = File.Data(captureNum).ChargeInjection;
amplitude_use = sprintf('%g uA',amplitude);
phaseWidth_use = sprintf('%g us',phaseWidth);
chargePhase_use = sprintf('%g nC/ph',chargePhase);
chargeInjection_use = sprintf('%g mC/cm2',chargeInjection);
parameters_cell = {...
    amplitude_use;...
    phaseWidth_use;...
    chargePhase_use;...
    chargeInjection_use};
parameters_len = length(parameters_cell);
idx_shift = idx_shift + 1;
data_cell(1:parameters_len,totalChannels+idx_shift) = parameters_cell;
tableHeadings_alloc = [tableHeadings,'Parameters'];
tableHeadings = tableHeadings_alloc;

% Date Time
dateTime = File.Data(captureNum).DateTime;
dateTime_cell{1} = dateTime;
idx_shift = idx_shift + 1;
data_cell(:,totalChannels+idx_shift) = dateTime_cell;
tableHeadings_alloc = [tableHeadings,'Date Time'];
tableHeadings = tableHeadings_alloc;
dataTable = cell2table( ...
    data_cell, ...
    'VariableNames',tableHeadings);
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

fprintf('\tSaving .csv file...');
startTime = tic;
writetable(dataTable,filepath_csv);    % save sheet to .csv file
[endTime,unit] = getEndTime(startTime);
fprintf('"%s" (%.2f %s)\n',filename_csv,endTime,unit);  % .csv file saved

fprintf('\tSaving .xlsx file...');
startTime = tic;
writetable(dataTable,filepath_xlsx,'Sheet',name_fix);  % save sheet to .xlsx file
[endTime,unit] = getEndTime(startTime);
fprintf('"%s" (%.2f %s)\n',filename_xlsx,endTime,unit); % .xlsx file saved

end