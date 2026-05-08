function [] = saveAnimalChannelData(...
    filepath,...
    filename,...
    channelNum,...
    data,...
    vtPlot)
%% Constants
N_TO_MICRO = 1e6;

%% Function
fprintf('Saving files...\n');

% Capture data of channel
time = data(channelNum).Time * N_TO_MICRO;              % time data array
voltage = data(channelNum).VoltageTransient;            % voltage data array
current = data(channelNum).CurrentStimulation;          % current data array
chargePhase = data(channelNum).ChargePhase;      	% charge per phase value
chargeInj = data(channelNum).ChargeInjection;           % charge injection value
maxPotential = data(channelNum).MaxPotentialExcursion;   % maximum cathodal potential value
voltageDrive = data(channelNum).DrivingVoltage;           % driving voltage value
dateTime = data(channelNum).DateTime;                   % date and time completed
status = data(channelNum).Status;

% Arrays for single values
blank_len = length(time);          % length of data
blank_array = strings(blank_len,1);	% array of blank space
chargePerPh_strings = blank_array;
chargePerPh_strings(1) = string(chargePhase);     	% add charge per phase to array
chargeInj_strings = blank_array;
chargeInj_strings(1) = string(chargeInj);          % add charge injection to array
maxPotential_strings = blank_array;
maxPotential_strings(1) = string(maxPotential);        % add max potential to array
voltageDrive_strings = blank_array;
voltageDrive_strings(1) = string(voltageDrive);        % add driving voltage to array
dateTime_strings = blank_array;
dateTime_strings(1) = dateTime;                        % add date and time to array
status_strings = blank_array;
status_strings(1) = status;

% Table
tableHeading = {...     % heading
    'Time (us)',...     % time
    'Voltage (V)',...   % voltage transient
    'Current (uA)',...  % current stimulation
    'Qph (nC/ph)',...   % charge per phase
    'Qinj (uC/cm2)',... % charge injection
    'Emc (V)',...       % max potential
    'Vdrive (V)',...    % driving voltage
    'Completed On'      % date and time
    'Status'};          % status
tableData = table(...           % table
    time,...                    % time
    voltage,...                 % voltage transient
    current,...                 % current stimulation
    chargePerPh_strings,...     % Qph
    chargeInj_strings,...       % Qinj
    maxPotential_strings,...    % Emc
    voltageDrive_strings,...    % Vdrive
    dateTime_strings,...        % DateTime
    status_strings,...          % status
    'VariableNames',tableHeading);

% Filename
filename_char = sprintf('%s_Ch%02d_%02dnC',filename,channelNum,chargePhase);  % filename for .mat file (char)

% Making .mat file
file_mat_char = sprintf('%s.mat',filename_char);  % filename for .mat file (char)
file_mat = convertCharsToStrings(file_mat_char);             	% filename string for .mat file (string)
fprintf('Saving %s...',file_mat);
file_mat_save = fullfile(filepath,file_mat);% save path for .mat file
save(file_mat_save,'data');          % save .mat file
% fprintf('%s\n',file_mat);                       % .mat file saved
fprintf('OK.\n');

% Make filename for .csv file
file_csv_char = sprintf('%s.csv',filename_char);  % filename for .csv file (char)
file_csv = convertCharsToStrings(file_csv_char);             	% filename string for .csv file (string)
file_csv_save = fullfile(filepath,file_csv);                    % save path for .csv file

% Save spreadsheet
fprintf('Saving %s...',file_csv);	% .csv file saved
writetable(tableData,file_csv_save);% save .csv file
fprintf('OK.\n');

% Save figure
if ishandle(vtPlot)
    file_fig_char = sprintf('%s.tiff',filename_char);  % filename for .tif file (char)
    file_fig = convertCharsToStrings(file_fig_char);             	% filename string for .tif file (string)
    file_tif = fullfile(filepath,file_fig);
    fprintf('Saving %s...',file_csv);           % .csv file saved
    print(vtPlot,'-dtiffn',file_tif,'-r300');
    fprintf('OK.\n');   % .tif file saved
end
% fprintf('All files saved.\n');% all files saved

end