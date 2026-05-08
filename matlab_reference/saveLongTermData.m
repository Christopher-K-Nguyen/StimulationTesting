function [filename_xlsx_save] = saveLongTermData(...
    chargePerPhase,...
    name,...
    listChannel,...
    channelSelect,...
    numOfChannel,...
    savePath,...
    matLongTermPulsing,...
    longTermCycles,...  % long-term cycles
    periodicCycle,...   % periodic cycle
    pauseCycle)         % pause cycle)
%% Function
fprintf('Saving files...\n');

% Starting filename
filename_char = sprintf('%s_LongTerm_%02gnC',name,chargePerPhase);  % formatted variables

% Making .mat file
filename_mat = append(filename_char,'.mat');        % filename for .mat file
filename_mat_save = fullfile(savePath,filename_mat);% save path for .mat file
save(filename_mat_save,...                          % save .mat file
    'matLongTermPulsing',...
    'longTermCycles',...
    'periodicCycle',...
    'pauseCycle');       
fprintf('%s\n',filename_mat);                       % .mat file saved

% Making spreadsheet
filename_xlsx = append(filename_char,'.xlsx');           % filename for .xlsx file
filename_xlsx_save = fullfile(savePath,filename_xlsx); 	% save path for .xlsx file
for channel_idx = 1:numOfChannel                % loop through channel data
    channelNum = channelSelect(channel_idx);
    % Capture data of channel
    time_data = matLongTermPulsing(channelNum).Time;                        % time data array
    cycles_data = matLongTermPulsing(channelNum).Cycles;                    % cycles data array
    maxPotential_data = matLongTermPulsing(channelNum).MaxCathodalPotential;% maximum cathodal potential value
    voltageDrive_data = matLongTermPulsing(channelNum).VoltageDrive;        % driving voltage value
    dateTime_data = matLongTermPulsing(channelNum).DateTime;                % date and time completed
    
    % Headings for table
    tableHeading = {...     % heading
        'Time (s)',...      % time
        'Cycles',...        % cycles
        'Emc (V)',...       % max cathodal potential
        'Vdrive (V)',...    % driving voltage
        'Completed On'};    % date and time
    
    % Data table
    tableData = table(...               % table
        time_data,...                   % table
        cycles_data,...                 % cycles
        maxPotential_data,...           % Emc
        voltageDrive_data,...           % Vdrive
        dateTime_data,...               % DateTime
        'VariableNames',tableHeading);            
    
%     % Make filename for .csv file
    channelName = listChannel(channelNum);
    replaceStim_char = strrep(channelName,'Stimulator ','Stim');  % formatted channel
    replaceCH_char = strrep(replaceStim_char,'Channel ','CH');  % formatted channel
%     filename_csv_char = sprintf('%s_Ch%02d.csv',filename_char,channelNum);  % filename for .csv file (char)
%     filename_csv = convertCharsToStrings(filename_csv_char);             	% filename string for .csv file (string)
%     filename_csv_save = fullfile(savePath,filename_csv);               	% save path for .csv file
    
    % Save spreadsheets
%     writetable(tableData,filename_csv_save);    % save .csv file
%     fprintf('%s\n',filename_csv);               % .csv file saved
    channelName_str = convertCharsToStrings(replaceCH_char);        % channel number (string)
    writetable(tableData,filename_xlsx_save,'Sheet',channelName_str);% save sheet to .xlsx file
end
fprintf('%s\n',filename_xlsx);	% .xlsx file saved
fprintf('All files saved.\n\n');% all files saved

end